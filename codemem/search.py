"""Hybrid search: FTS5 BM25 (lexical) fused with Ollama embeddings (semantic) by reciprocal rank fusion.

BM25 is the tf-idf family; FTS5 does it natively and handles stemming. The embedding leg catches
"fetch a page with retries" matching "http-client". If Ollama is unreachable, we fall back to
BM25 only and say so in the result.
"""
import hashlib, json, math, struct, threading, urllib.request
from . import config, db
from .db import q, one, tx, MATURITY_RANK

_embed_lock = threading.Lock()
_ollama_ok = None


def _vec_pack(v):
    return struct.pack(f"{len(v)}f", *v)


def _vec_unpack(b):
    return struct.unpack(f"{len(b) // 4}f", b)


def embed(texts):
    """Return list of vectors or None if Ollama is unavailable."""
    global _ollama_ok
    if not config.EMBED_ENABLED:
        return None
    try:
        req = urllib.request.Request(f"{config.OLLAMA_URL}/api/embed",
                                     data=json.dumps({"model": config.EMBED_MODEL, "input": texts}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.load(r)
        _ollama_ok = True
        return out.get("embeddings")
    except Exception:
        _ollama_ok = False
        return None


def fts_escape(query):
    """Turn free text into a safe FTS5 query: each token quoted, OR'd, with prefix matching."""
    toks = [t for t in "".join(ch if ch.isalnum() or ch in "_-" else " " for ch in query).split() if t]
    if not toks:
        return None
    return " OR ".join(f'"{t}"*' for t in toks[:12])


def bm25(query, kinds=None, project=None, limit=30, exclude_audience=None, exclude_maturity=None, include_vendor=False):
    fq = fts_escape(query)
    if not fq:
        return []
    sql = "SELECT kind, ref_id, project, title, maturity, bm25(search_index) AS score FROM search_index WHERE search_index MATCH ?"
    params = [fq]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        params += list(kinds)
    if project:
        sql += " AND project=? COLLATE NOCASE"
        params.append(project)
    if exclude_audience:
        sql += f" AND audience NOT IN ({','.join('?' * len(exclude_audience))})"
        params += list(exclude_audience)
    if exclude_maturity:
        sql += f" AND maturity NOT IN ({','.join('?' * len(exclude_maturity))})"
        params += list(exclude_maturity)
    if not include_vendor:
        sql += " AND origin != 'vendor'"
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    return q(sql, params)


def embed_pending(max_items=200):
    """Embed index rows that have no vector yet. Called in the background after writes and by the CLI."""
    if not config.EMBED_ENABLED:
        return 0
    with _embed_lock:
        rows = q("""SELECT s.kind, s.ref_id, s.title, s.body, s.tags FROM search_index s
                    LEFT JOIN embedding e ON e.kind=s.kind AND e.ref_id=s.ref_id
                    WHERE e.kind IS NULL AND s.kind != 'commit' LIMIT ?""", (max_items,))
        if not rows:
            return 0
        done = 0
        for i in range(0, len(rows), 16):
            batch = rows[i:i + 16]
            texts = [f"{r['title']}\n{r['tags']}\n{(r['body'] or '')[:2000]}" for r in batch]
            vecs = embed(texts)
            if not vecs:
                return done
            with tx() as c:
                for r, v in zip(batch, vecs):
                    c.execute("INSERT OR REPLACE INTO embedding(kind, ref_id, model, hash, vec) VALUES (?,?,?,?,?)",
                              (r["kind"], r["ref_id"], config.EMBED_MODEL,
                               hashlib.sha1(texts[0].encode()).hexdigest(), _vec_pack(v)))
            done += len(batch)
        return done


def semantic(query, kinds=None, project=None, limit=30, exclude_audience=None, exclude_maturity=None, include_vendor=False):
    vecs = embed([query])
    if not vecs:
        return []
    qv = vecs[0]
    qn = math.sqrt(sum(x * x for x in qv)) or 1.0
    sql = "SELECT e.kind, e.ref_id, e.vec, s.project, s.title, s.maturity FROM embedding e JOIN search_index s ON s.kind=e.kind AND s.ref_id=e.ref_id"
    where, params = [], []
    if kinds:
        where.append(f"e.kind IN ({','.join('?' * len(kinds))})"); params += list(kinds)
    if project:
        where.append("s.project=? COLLATE NOCASE"); params.append(project)
    if exclude_audience:
        where.append(f"s.audience NOT IN ({','.join('?' * len(exclude_audience))})"); params += list(exclude_audience)
    if exclude_maturity:
        where.append(f"s.maturity NOT IN ({','.join('?' * len(exclude_maturity))})"); params += list(exclude_maturity)
    if not include_vendor:
        where.append("s.origin != 'vendor'")
    if where:
        sql += " WHERE " + " AND ".join(where)
    scored = []
    for r in q(sql, params):
        v = _vec_unpack(r["vec"])
        dot = sum(a * b for a, b in zip(qv, v))
        vn = math.sqrt(sum(x * x for x in v)) or 1.0
        scored.append((dot / (qn * vn), r))
    scored.sort(key=lambda x: -x[0])
    return [{"kind": r["kind"], "ref_id": r["ref_id"], "project": r["project"], "title": r["title"], "maturity": r["maturity"], "score": s}
            for s, r in scored[:limit]]


def hydrate(kind, ref_id):
    if kind == "project":
        return one("SELECT * FROM project WHERE id=?", (ref_id,))
    if kind == "asset":
        return one("SELECT a.*, p.name AS project FROM asset a LEFT JOIN project p ON p.id=a.project_id WHERE a.id=?", (ref_id,))
    if kind == "note":
        return one("SELECT n.*, p.name AS project FROM note n LEFT JOIN project p ON p.id=n.project_id WHERE n.id=?", (ref_id,))
    if kind == "location":
        return one("SELECT l.*, p.name AS project, p.name || ' on ' || l.machine AS title FROM location l JOIN project p ON p.id=l.project_id WHERE l.id=?", (ref_id,))
    if kind == "doc":
        return one("SELECT id, source, title, substr(body,1,600) AS excerpt, updated_at FROM doc WHERE id=?", (ref_id,))
    if kind == "commit":
        return one('SELECT c.*, p.name AS project FROM "commit" c JOIN project p ON p.id=c.project_id WHERE c.id=?', (ref_id,))
    return None


def search(query, kinds=None, project=None, limit=10, mode="hybrid", exclude_audience=None, exclude_maturity=None, include_vendor=False):
    """Returns {'results': [...], 'mode': 'hybrid'|'bm25'} with hydrated records.
    exclude_audience: project audiences to leave out (e.g. ["unrestricted"] in a professional context).
    exclude_maturity: ratings to leave out (e.g. ["junk","broken","sunset"]). Ratings also weight the
    ranking: authoritative floats up, junk sinks, so unrated and rated results still mix sensibly."""
    lex = bm25(query, kinds, project, limit=40, exclude_audience=exclude_audience, exclude_maturity=exclude_maturity, include_vendor=include_vendor)
    sem = semantic(query, kinds, project, limit=40, exclude_audience=exclude_audience, exclude_maturity=exclude_maturity, include_vendor=include_vendor) if mode != "bm25" else []
    used = "hybrid" if sem else "bm25"
    fused, mat = {}, {}
    for rank, r in enumerate(lex):
        k = (r["kind"], r["ref_id"])
        fused[k] = fused.get(k, 0) + 1.0 / (60 + rank); mat[k] = r["maturity"] or ""
    for rank, r in enumerate(sem):
        k = (r["kind"], r["ref_id"])
        fused[k] = fused.get(k, 0) + 1.0 / (60 + rank); mat[k] = r["maturity"] or ""
    trust = {}
    for kind, table in (("project", "project"), ("asset", "asset")):
        ids = [ref for (kd, ref) in fused if kd == kind]
        if ids:
            for r in q(f"SELECT id, trust FROM {table} WHERE id IN ({','.join('?' * len(ids))})", ids):
                trust[(kind, r["id"])] = r["trust"]
    for k in fused:
        fused[k] *= MATURITY_RANK.get(mat[k], 1.0)
        t = trust.get(k)
        if t is not None:
            fused[k] *= 0.85 + 0.3 * t / 100   # trust is inferred: a milder nudge than maturity
    ordered = sorted(fused.items(), key=lambda kv: -kv[1])[:limit]
    out = []
    for (kind, ref_id), score in ordered:
        rec = hydrate(kind, ref_id)
        if rec is None:
            continue
        rec = dict(rec)
        rec.pop("vec", None)
        if kind == "doc" or kind == "note":
            rec["body"] = (rec.get("body") or rec.get("excerpt") or "")[:1200]
        out.append({"kind": kind, "score": round(score, 4), "record": rec})
    return {"query": query, "mode": used, "results": out}


def embed_in_background():
    t = threading.Thread(target=embed_pending, daemon=True)
    t.start()

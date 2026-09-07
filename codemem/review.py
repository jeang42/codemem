"""Targeted model review. Not a blanket summarizer: it reads code only where the parser cannot answer.

Stage 1  near-duplicates: same function name in two projects, different normalized hash, similar
         size. The model reads both bodies and rules same | variant | different, with what differs.
         Confirmed pairs become shares-code-with link notes prefixed "model:".
Stage 2  thin descriptions on trusted assets (trust >= 60, description Unclear/auto-drafted/short).
         The model reads the file (up to ~250 lines) and writes description, what it does, risks
         (hardcoded paths, swallowed exceptions, TODO/FIXME, embedded secrets), and reuse notes.
         Stored as asset.review (JSON) + reviewed_at, tag model-reviewed; description replaced only
         if it was a placeholder.

    codemem review [--stage 1|2] [--limit N] [--dry-run]
Progress goes to the log line by line so a long run can be watched.
"""
import json, re, time, urllib.request
from pathlib import Path
from . import config
from .db import q, one, tx, now
from .store import upsert_asset, add_link, get_project
from .discover import repo_for_project
from .discover_core import _git

MODEL = "qwen3-coder:30b"
MAX_LINES = 250


def ask(prompt, timeout=300):
    req = urllib.request.Request(f"{config.OLLAMA_URL}/api/chat", data=json.dumps({
        "model": MODEL, "stream": False, "format": "json", "options": {"temperature": 0.1, "num_ctx": 16384},
        "messages": [{"role": "user", "content": prompt}]}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(json.load(r)["message"]["content"])


def asset_text(a):
    """File content for an asset: local path on this machine, else the blob from the project's bare repo."""
    if a["machine"] == config.MACHINE and a["path"] and Path(a["path"]).is_file():
        try:
            return Path(a["path"]).read_text(errors="replace")
        except OSError:
            return ""
    p = one("SELECT * FROM project WHERE id=?", (a["project_id"],))
    if p:
        repo, bare = repo_for_project(p)
        if repo and a["blob_hash"]:
            raw = _git(repo, "cat-file", "-p", a["blob_hash"], binary=True)
            if raw:
                return raw.decode("utf-8", "replace")
    return a["source_head"] or ""   # shipped by a remote agent (first 250 lines)


def function_source(text, name):
    """Extract one function's source by name (last component of Class.method)."""
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ""
    short = name.split(".")[-1]
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == short:
            return "\n".join(text.splitlines()[n.lineno - 1:n.end_lineno])
    return ""


# ---- stage 1 -----------------------------------------------------------------

def near_duplicate_candidates(limit):
    rows = q("""SELECT a.id, a.name, a.path, a.func_hashes, a.machine, a.blob_hash, a.project_id, p.name AS project
                FROM asset a JOIN project p ON p.id=a.project_id WHERE p.origin='own' AND a.func_hashes NOT IN ('', '[]')""")
    byname = {}
    for r in rows:
        try:
            funcs = json.loads(r["func_hashes"])
        except ValueError:
            continue
        for f in funcs:
            if f.get("lines", 0) >= 10:
                byname.setdefault(f["name"].split(".")[-1], []).append((r, f))
    seen, out = set(), []
    for fname, hits in byname.items():
        if len(fname) < 6 or fname.startswith("_") and len(fname) < 8 or fname in ("main", "setup", "run", "process", "handle", "execute", "__init__"):
            continue
        for i, (ra, fa) in enumerate(hits):
            for rb, fb in hits[i + 1:]:
                if ra["project"] == rb["project"] or fa["hash"] == fb["hash"]:
                    continue
                if not (0.6 <= fa["lines"] / max(fb["lines"], 1) <= 1.6):
                    continue
                key = tuple(sorted([ra["project"], rb["project"]])) + (fname,)
                if key in seen:
                    continue
                seen.add(key)
                out.append((fname, ra, fa, rb, fb))
    # pairs between projects already linked as sharing code are the likeliest real duplicates: first
    linked = set()
    for l in q("SELECT (SELECT name FROM project WHERE id=from_id) a, (SELECT name FROM project WHERE id=to_id) b FROM link WHERE relation='shares-code-with'"):
        linked.add(tuple(sorted([l["a"], l["b"]])))
    out.sort(key=lambda c: (tuple(sorted([c[1]["project"], c[3]["project"]])) not in linked, -min(c[2]["lines"], c[4]["lines"])))
    return out[:limit]


def stage1(limit=40, dry=False, log=print):
    cands = near_duplicate_candidates(limit)
    log(f"stage 1: {len(cands)} near-duplicate candidate pairs")
    results = {"same": 0, "variant": 0, "different": 0, "skipped": 0}
    for i, (fname, ra, fa, rb, fb) in enumerate(cands, 1):
        sa, sb = function_source(asset_text(ra), fa["name"]), function_source(asset_text(rb), fb["name"])
        if not sa or not sb:
            results["skipped"] += 1; log(f"  [{i}/{len(cands)}] {fname}: source unavailable, skipped"); continue
        prompt = f"""Two Python functions named {fname} from different projects. Decide if they do the same job.
Answer JSON: {{"verdict": "same"|"variant"|"different", "difference": "<one sentence: what B does differently from A, or 'none'>", "better": "A"|"B"|"neither", "why": "<one sentence>"}}
"same" = interchangeable. "variant" = same purpose, meaningful differences. "different" = only the name is shared.

A ({ra['project']} / {ra['path']}):
{sa[:6000]}

B ({rb['project']} / {rb['path']}):
{sb[:6000]}"""
        t0 = time.time()
        try:
            v = ask(prompt)
        except Exception as e:
            results["skipped"] += 1; log(f"  [{i}/{len(cands)}] {fname}: model error {e}"); continue
        verdict = v.get("verdict", "different")
        results[verdict if verdict in results else "different"] += 1
        log(f"  [{i}/{len(cands)}] {fname}: {ra['project']} vs {rb['project']} -> {verdict} ({time.time()-t0:.0f}s) {v.get('difference','')[:100]}")
        if dry or verdict == "different":
            continue
        pa, pb = get_project(ra["project"]), get_project(rb["project"])
        a_, b_ = sorted([pa, pb], key=lambda p: p["name"])
        existing = one("SELECT note FROM link WHERE from_kind='project' AND from_id=? AND to_kind='project' AND to_id=? AND relation='shares-code-with'", (a_["id"], b_["id"]))
        line = f"model: {fname} is {'the same' if verdict == 'same' else 'a variant'} in {Path(ra['path']).name} and {Path(rb['path']).name}" + \
               (f" ({v.get('difference')})" if verdict == "variant" and v.get("difference") else "") + \
               (f"; prefer {ra['project'] if v.get('better') == 'A' else rb['project']}: {v.get('why')}" if v.get("better") in ("A", "B") else "")
        note = existing["note"] if existing else ""
        if line[:60] not in note:
            note = "; ".join(x for x in [note, line] if x)[:2000]
        add_link("project", a_["id"], "project", b_["id"], "shares-code-with", note)
    log(f"stage 1 done: {results}")
    return results


# ---- stage 2 -----------------------------------------------------------------

def thin_candidates(limit, machine=""):
    if machine:   # everything Python on one machine, best trust first (source shipped by its agent)
        return q("""SELECT a.*, p.name AS project FROM asset a JOIN project p ON p.id=a.project_id
                    WHERE p.origin='own' AND a.review='' AND a.path LIKE '%.py' AND a.machine=?
                    ORDER BY COALESCE(a.trust,0) DESC LIMIT ?""", (machine, limit))
    return q("""SELECT a.*, p.name AS project FROM asset a JOIN project p ON p.id=a.project_id
                WHERE p.origin='own' AND a.trust >= 60 AND a.review='' AND a.path LIKE '%.py'
                AND (a.description LIKE 'Unclear:%' OR (',' || a.tags || ',') LIKE '%,auto-described,%' OR length(a.description) < 40)
                ORDER BY a.trust DESC LIMIT ?""", (limit,))


def stage2(limit=40, dry=False, log=print, machine=""):
    cands = thin_candidates(limit, machine)
    log(f"stage 2: {len(cands)} trusted assets with thin descriptions")
    done = risky = 0
    for i, a in enumerate(cands, 1):
        text = asset_text(a)
        if not text:
            log(f"  [{i}/{len(cands)}] {a['name']}: source unavailable (other machine?), skipped"); continue
        lines = text.splitlines()
        body = "\n".join(lines[:MAX_LINES]) + (f"\n... ({len(lines) - MAX_LINES} more lines)" if len(lines) > MAX_LINES else "")
        prompt = f"""Review this Python file from project "{a['project']}" for a catalogue of reusable code. Be concrete and plain; no praise.
Answer JSON:
{{"description": "<one sentence, max 25 words: what it is and does>",
  "what_it_does": "<2-3 sentences: inputs, outputs, side effects, what it talks to>",
  "risks": ["<each concrete concern: hardcoded paths/hosts, credentials in code, bare/swallowed exceptions, TODO/FIXME, no error handling, destructive operations>"],
  "reuse": "<one sentence: how to reuse it or what would need changing first>",
  "confidence": "high"|"medium"|"low"}}

File {a['path']}:
{body[:14000]}"""
        t0 = time.time()
        try:
            v = ask(prompt)
        except Exception as e:
            log(f"  [{i}/{len(cands)}] {a['name']}: model error {e}"); continue
        risks = [r for r in (v.get("risks") or []) if isinstance(r, str) and r.strip()]
        risky += bool(risks)
        log(f"  [{i}/{len(cands)}] {a['name']} ({time.time()-t0:.0f}s): {v.get('description','')[:90]} | risks: {len(risks)}")
        if dry:
            continue
        review = {"what_it_does": v.get("what_it_does", ""), "risks": risks, "reuse": v.get("reuse", ""),
                  "confidence": v.get("confidence", ""), "model": MODEL}
        fields = {"review": json.dumps(review), "reviewed_at": now()}
        placeholder = a["description"].startswith("Unclear:") or "auto-described" in (a["tags"] or "") or len(a["description"]) < 40
        if placeholder and v.get("description"):
            fields["description"] = v["description"].strip()[:300]
        tags = [t for t in (a["tags"] or "").split(",") if t and t != "auto-described"] + ["model-reviewed"]
        fields["tags"] = ",".join(dict.fromkeys(tags))
        upsert_asset(a["name"], a["kind"], **fields)
        done += 1
    log(f"stage 2 done: {done} reviewed, {risky} with risks flagged")
    return {"reviewed": done, "with_risks": risky}

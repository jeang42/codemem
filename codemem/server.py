"""codemem MCP server. Streamable HTTP at /mcp, JSON API under /api, web UI at /.

Tools are thin: validate, call store/search, return JSON-able dicts. Every argument has a default
(some MCP clients reject calls that omit a required parameter).
"""
import json, os
from pathlib import Path
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, PlainTextResponse
from mcp.server.fastmcp import FastMCP

from . import config, search as S, gitsync, scan as SC, knowledge, trust as T
from .db import q, one, tx, now, connect, MATURITY
from . import store
from .store import (get_project, upsert_project, upsert_asset, add_link, upsert_scan_root,
                    project_name_from_remote)

mcp = FastMCP("codemem", host=config.HOST, port=config.PORT,
              instructions="Memory for every coding project on every machine: projects, reusable assets, "
                           "session notes, decisions, commits, and how-to knowledge. Search before building. "
                           "Register what you build. Log the session when done.")

WEB = Path(__file__).resolve().parent / "web"


def _aud(exclude_audience):
    if not exclude_audience:
        return None
    if isinstance(exclude_audience, str):
        return [a.strip() for a in exclude_audience.split(",") if a.strip()]
    return list(exclude_audience)


def _project_from_path(path, machine):
    """Resolve a working directory to a project: exact location, parent location, or git remote name."""
    if not path:
        return None
    loc = one("SELECT project_id FROM location WHERE machine=? AND path=?", (machine, path))
    if loc:
        return one("SELECT * FROM project WHERE id=?", (loc["project_id"],))
    rows = q("SELECT project_id, path FROM location WHERE machine=? AND ? LIKE path || '/%' ORDER BY length(path) DESC LIMIT 1",
             (machine, path))
    if rows:
        return one("SELECT * FROM project WHERE id=?", (rows[0]["project_id"],))
    if Path(path).is_dir():
        remote = SC._git(path, "remote", "get-url", "origin")
        name = project_name_from_remote(remote) or Path(path).name
        return get_project(name)
    return get_project(Path(path).name)


def brief(project, days=30, limit_notes=8, limit_commits=10):
    p = project
    notes = q("SELECT id, kind, title, substr(body,1,700) AS body, tags, machine, created_at FROM note WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
              (p["id"], limit_notes))
    commits = q('SELECT hash, author, date, substr(message,1,200) AS message, pushed_from FROM "commit" WHERE project_id=? ORDER BY date DESC LIMIT ?',
                (p["id"], limit_commits))
    assets = q("SELECT id, name, kind, path, machine, description, usage, tags, maturity, maturity_note, trust, trust_breakdown, verified_at, last_changed, change_count, review, reviewed_at FROM asset WHERE project_id=? ORDER BY COALESCE(trust,0) DESC, updated_at DESC", (p["id"],))
    locations = q("SELECT machine, path, branch, dirty, last_local_commit, languages, key_files, last_scanned FROM location WHERE project_id=?", (p["id"],))
    import sys
    deps = {}
    for a in q("SELECT imports FROM asset WHERE project_id=? AND imports!=''", (p["id"],)):
        for m in a["imports"].split(","):
            if m and m not in sys.stdlib_module_names:
                deps[m] = deps.get(m, 0) + 1
    links = q("""SELECT l.relation, l.note, l.from_kind, l.from_id, l.to_kind, l.to_id,
                        CASE l.from_kind WHEN 'project' THEN (SELECT name FROM project WHERE id=l.from_id) WHEN 'asset' THEN (SELECT name FROM asset WHERE id=l.from_id) ELSE '' END AS from_name,
                        CASE l.to_kind WHEN 'project' THEN (SELECT name FROM project WHERE id=l.to_id) WHEN 'asset' THEN (SELECT name FROM asset WHERE id=l.to_id) ELSE '' END AS to_name
                 FROM link l WHERE (l.from_kind='project' AND l.from_id=?) OR (l.to_kind='project' AND l.to_id=?)""",
              (p["id"], p["id"]))
    return {"project": dict(p), "locations": locations, "assets": assets, "notes": notes, "commits": commits, "links": links,
            "dependencies": sorted(deps, key=lambda k: -deps[k])[:25]}


def _trust_words(rec):
    try:
        br = json.loads(rec.get("trust_breakdown") or "{}")
    except ValueError:
        return ""
    parts = [f"{k} {v}" for k, v in br.items() if isinstance(v, int)]
    flags = [k for k, v in br.items() if v is True]
    s = "(" + ", ".join(parts) + ")" if parts else ""
    if flags:
        s += " " + " ".join(f"[{f}]" for f in flags)
    if rec.get("verified_at"):
        s += f" verified {rec['verified_at'][:10]}"
    return s


def brief_text(b):
    """Compact markdown for the SessionStart hook. Keep it short: it lands in every session's context."""
    p = b["project"]
    out = [f"# codemem: {p['name']}"]
    if p.get("description"):
        out.append(p["description"])
    if p.get("purpose"):
        out.append(f"Purpose: {p['purpose']}")
    if p.get("maturity"):
        out.append(f"**Maturity: {p['maturity']}**" + (f" ({p['maturity_note']})" if p.get("maturity_note") else ""))
    if p.get("trust") is not None:
        out.append(f"Trust {p['trust']}/100 " + _trust_words(p))
    meta = [f"status={p['status']}", f"audience={p['audience']}", f"visibility={p.get('visibility') or 'private'}"] + (["VENDOR CLONE (not our code)"] if p.get("origin") == "vendor" else [])
    if p.get("tags"):
        meta.append(f"tags={p['tags']}")
    if p.get("commit_count"):
        meta.append(f"commits={p['commit_count']} (last {p['last_commit'][:10] if p['last_commit'] else '?'})")
    if p.get("gitea_url"):
        meta.append(p["gitea_url"])
    out.append(" | ".join(meta))
    if b["locations"]:
        out.append("Locations: " + "; ".join(f"{l['machine']}:{l['path']}" + (" (dirty)" if l["dirty"] else "") for l in b["locations"]))
    if b["assets"]:
        out.append("Reusable assets here:")
        out += [f"- {a['name']} [{a['kind']}]" + (f" ({a['maturity']})" if a.get("maturity") else "") + (f" trust {a['trust']}" if a.get("trust") is not None else "") + f" {a['description'][:120]}" for a in b["assets"][:8]]
    if b.get("dependencies"):
        out.append("Third-party imports: " + ", ".join(b["dependencies"][:15]))
    if b["links"]:
        out.append("Links: " + "; ".join(f"{l['from_name']} {l['relation']} {l['to_name']}" for l in b["links"][:8]))
    if b["notes"]:
        out.append("Recent notes:")
        for n in b["notes"][:5]:
            first = (n["body"] or "").strip().splitlines()
            out.append(f"- {n['created_at'][:10]} [{n['kind']}] {n['title']}: {first[0][:160] if first else ''}")
    if b["commits"]:
        out.append("Recent commits: " + "; ".join(f"{c['date'][:10]} {c['message'].splitlines()[0][:70]}" for c in b["commits"][:5]))
    out.append("(Use codemem tools: search, find_assets, register_asset, log_session, howto.)")
    return "\n".join(out)


# ---- MCP tools ---------------------------------------------------------------

HELP = """codemem: memory for every coding project on every machine. Web UI http://localhost:8055/

WORKFLOW
  start of a task   search("what you are about to build")  and  find_assets("...")   -> reuse before rebuilding
  who uses a lib    find_assets(imports="chromadb")   -> exact, from parsed imports
  need a procedure  howto("publish") / howto("add machine") / howto() lists all
  about a project   project_brief("name" or "/path")
  built something   register_asset(name, kind, description, usage, project)   usage = the one line to reuse it
  judged something  rate(name, maturity, note)   or update_project(..., maturity=, maturity_note=)
  ran it, it works  verify(name [, asset_kind], note)   -> restarts the trust freshness clock
  replacing old code handoff(name, stage, old, new, promotion_class, report, note)  ->  list_handoffs()
  connected things  link_items(from_project, to_project, relation)   uses|could-reuse|supersedes|derived-from|related
  end of session    log_session(project, summary, decisions, resources, dead_ends, next_steps)
  brief said "no record of <dir>"  update_project(name, description, path="<dir>")

TOOLS
  search list_projects project_brief update_project register_asset find_assets rate verify link_items
  handoff list_handoffs log_session add_note howto activity add_scan_root scan_path stats help

LABELS
  audience  unrestricted (default: personal work, unfiltered) | professional | employer
            filter with exclude_audience="unrestricted" in a professional context; never filtered on its own
  origin    own | vendor (third-party clones; HIDDEN from search/list unless include_vendor=True / origin="all")
  visibility private (default) | shared | public: who may see it; list_projects(visibility=)
  maturity  authoritative > usable > experimental > (unrated) > antiquated > sunset > broken > junk
            weights search ranking; drop with exclude_maturity="junk,broken,sunset"
  trust     computed 0-100 (freshness, activity, hygiene, deployed, reuse, review grade; assets add churn,
            description, curated, project). Maturity caps/floors it. Mild ranking nudge. See trust_breakdown.
  status    active | paused | done | abandoned | archived
  tags      free comma list. auto-described marks a model-drafted placeholder description.

DATA SOURCES  git server push hook (commits within seconds), Gitea metadata, local scans of registered roots,
  ingested docs (CODEMEM_DOC_SOURCES), Claude Code SessionStart/SessionEnd hooks, and what you write here.
Docs: docs/USER_GUIDE.md in the codemem repo, also ingested and searchable.
"""


@mcp.tool()
def help(topic: str = "") -> dict:
    """How to use codemem: workflow, tool list, label vocabularies, data sources. Call with no arguments
    for the overview; a topic (e.g. "maturity", "audience", "vendor", "session") narrows it. Cheap: no DB access."""
    if not topic.strip():
        return {"help": HELP}
    t = topic.lower()
    lines = [l for l in HELP.splitlines() if t in l.lower()]
    extra = {"maturity": MATURITY}.get(t, None)
    return {"topic": topic, "matches": lines or ["nothing matched; call help() for the overview"], "detail": extra}


@mcp.tool()
def search(query: str = "", kinds: str = "", project: str = "", limit: int = 10, exclude_audience: str = "",
           exclude_maturity: str = "", include_vendor: bool = False) -> dict:
    """Hybrid search (BM25 + embeddings) across projects, assets, notes, commits, locations and docs.
    kinds: comma list to restrict, e.g. "asset,note". exclude_audience: comma list of project audiences
    to leave out (e.g. "unrestricted" when working professionally). exclude_maturity: ratings to drop, e.g.
    "junk,broken,sunset" when you only want things safe to build on. Results carry a maturity field;
    authoritative ranks up, junk ranks down. Vendor clones (other people's repos) are left out unless
    include_vendor=True. Search BEFORE building something new."""
    if not query.strip():
        return {"error": "query is required"}
    ks = [k.strip() for k in kinds.split(",") if k.strip()] or None
    return S.search(query, ks, project or None, max(1, min(limit, 50)), exclude_audience=_aud(exclude_audience),
                    exclude_maturity=_aud(exclude_maturity), include_vendor=include_vendor)


@mcp.tool()
def project_brief(name_or_path: str = "", machine: str = "") -> dict:
    """Everything known about one project: description, locations on each machine, reusable assets,
    recent notes, recent commits, links. Accepts a project name or a working-directory path."""
    machine = machine or config.MACHINE
    p = get_project(name_or_path) if name_or_path and "/" not in name_or_path and "\\" not in name_or_path else None
    p = p or _project_from_path(name_or_path, machine)
    if not p:
        return {"error": f"no project matches {name_or_path!r}", "hint": "list_projects or update_project to create it"}
    return brief(p)


@mcp.tool()
def list_projects(status: str = "", tag: str = "", machine: str = "", audience: str = "", exclude_audience: str = "",
                  maturity: str = "", exclude_maturity: str = "", limit: int = 200, origin: str = "own", visibility: str = "") -> dict:
    """List projects with a one-line summary each. Filter by status, tag, machine (has a location there),
    audience, maturity (e.g. "authoritative") or exclude_maturity (e.g. "junk,antiquated").
    origin: "own" (default) | "vendor" (cloned third-party repos) | "all"."""
    sql = "SELECT p.id, p.name, p.description, p.status, p.audience, p.origin, p.visibility, p.maturity, p.maturity_note, p.trust, p.trust_breakdown, p.verified_at, p.tags, p.languages, p.commit_count, p.last_commit, p.gitea_url, p.github_url FROM project p"
    where, params = [], []
    if origin and origin != "all":
        where.append("p.origin=?"); params.append(origin)
    if visibility:
        where.append("p.visibility=?"); params.append(visibility)
    if maturity:
        where.append("p.maturity=?"); params.append(maturity)
    exm = _aud(exclude_maturity)
    if exm:
        where.append(f"COALESCE(p.maturity,'') NOT IN ({','.join('?' * len(exm))})"); params += exm
    if machine:
        sql += " JOIN location l ON l.project_id=p.id"
        where.append("l.machine=?"); params.append(machine)
    if status:
        where.append("p.status=?"); params.append(status)
    if tag:
        where.append("(',' || p.tags || ',') LIKE ?"); params.append(f"%,{tag.lower()},%")
    if audience:
        where.append("p.audience=?"); params.append(audience)
    ex = _aud(exclude_audience)
    if ex:
        where.append(f"p.audience NOT IN ({','.join('?' * len(ex))})"); params += ex
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY p.id ORDER BY COALESCE(p.last_commit, p.updated_at) DESC LIMIT ?"
    params.append(limit)
    rows = q(sql, params)
    return {"count": len(rows), "projects": rows}


@mcp.tool()
def update_project(name: str = "", description: str = "", purpose: str = "", status: str = "", tags: str = "",
                   audience: str = "", github_url: str = "", maturity: str = "", maturity_note: str = "",
                   origin: str = "", path: str = "", machine: str = "", visibility: str = "") -> dict:
    """Create or enrich a project. Only supplied fields change. status: active|paused|done|abandoned|archived.
    audience: unrestricted (default: personal work, unfiltered) | professional | employer (free text, used for
    filtering). origin: own | vendor (a third-party clone; hidden from search and lists by default). tags: comma list.
    visibility: private (default, the owner only) | shared (named people) | public (anyone): who may SEE the project.
    path (+ machine, default this server): record where the working copy lives, so the session-start brief finds it
    by directory. Use this when the brief says "no record of <cwd>".
    maturity: authoritative|usable|experimental|antiquated|sunset|broken|junk, with maturity_note saying why
    (e.g. "superseded by pipeline-v2"). Assets in the project inherit it unless rated themselves."""
    if not name:
        return {"error": "name is required"}
    if maturity and maturity not in MATURITY:
        return {"error": f"maturity must be one of {list(MATURITY)}", "meanings": MATURITY}
    if origin and origin not in ("own", "vendor"):
        return {"error": "origin must be own or vendor"}
    if visibility and visibility not in ("private", "shared", "public"):
        return {"error": "visibility must be private, shared or public"}
    p = upsert_project(name, description=description, purpose=purpose, status=status, tags=tags,
                       audience=audience, github_url=github_url, maturity=maturity, maturity_note=maturity_note, origin=origin,
                       visibility=visibility)
    out = dict(p)
    if path:
        m = machine or config.MACHINE
        facts = SC.describe(Path(path)) if m == config.MACHINE and Path(path).is_dir() else {}
        loc = store.upsert_location(p["id"], m, path, is_git=int(bool(facts.get("is_git"))),
                                    remote_url=facts.get("remote_url") or "", branch=facts.get("branch") or "",
                                    dirty=int(bool(facts.get("dirty"))), last_local_commit=facts.get("last_local_commit") or None,
                                    file_count=facts.get("file_count"), languages=facts.get("languages") or "",
                                    key_files=facts.get("key_files") or "", readme_head=facts.get("readme_head") or "")
        out["location"] = dict(loc)
    S.embed_in_background()
    return out


@mcp.tool()
def register_asset(name: str = "", kind: str = "", description: str = "", usage: str = "", project: str = "",
                   path: str = "", machine: str = "", tags: str = "", maturity: str = "", maturity_note: str = "") -> dict:
    """Record something reusable so it is found next time instead of rebuilt.
    kind: script|module|function|prompt|skill|mcp-server|docker|service|config|dataset|model|doc|tool|pattern.
    usage: the one line someone needs to reuse it (command, import, URL).
    maturity: authoritative|usable|experimental|antiquated|sunset|broken|junk (+ maturity_note why)."""
    if not name or not kind:
        return {"error": "name and kind are required"}
    if maturity and maturity not in MATURITY:
        return {"error": f"maturity must be one of {list(MATURITY)}", "meanings": MATURITY}
    a = upsert_asset(name, kind, project=project or None, description=description, usage=usage, path=path,
                     machine=machine or config.MACHINE, tags=tags, maturity=maturity, maturity_note=maturity_note)
    S.embed_in_background()
    return dict(a)


@mcp.tool()
def find_assets(query: str = "", kind: str = "", tag: str = "", project: str = "", exclude_audience: str = "",
                exclude_maturity: str = "", limit: int = 20, imports: str = "") -> dict:
    """Find reusable assets. With a query it searches; without, it lists (optionally by kind, tag, project).
    exclude_maturity="junk,broken,sunset,antiquated" leaves only things worth building on.
    imports="chromadb" answers "which files/projects use this library" exactly, from parsed import statements."""
    if imports.strip():
        mod = imports.strip().split(".")[0]
        rows = q("""SELECT a.id, a.name, a.kind, a.path, a.machine, a.trust, a.maturity, p.name AS project, p.audience
                    FROM asset a JOIN project p ON p.id=a.project_id
                    WHERE (',' || a.imports || ',') LIKE ? ORDER BY p.name, a.path LIMIT ?""", (f"%,{mod},%", limit * 10))
        ex = _aud(exclude_audience) or []
        rows = [r for r in rows if r["audience"] not in ex]
        return {"imports": mod, "projects": sorted({r["project"] for r in rows}), "assets": rows[:limit]}
    if query.strip():
        r = S.search(query, ["asset"], project or None, limit, exclude_audience=_aud(exclude_audience),
                     exclude_maturity=_aud(exclude_maturity))
        rows = [x["record"] for x in r["results"]]
        if kind:
            rows = [x for x in rows if x["kind"] == kind]
        return {"mode": r["mode"], "assets": rows}
    sql = "SELECT a.*, p.name AS project, p.audience, COALESCE(NULLIF(a.maturity,''), p.maturity) AS effective_maturity FROM asset a LEFT JOIN project p ON p.id=a.project_id"
    where, params = [], []
    exm = _aud(exclude_maturity)
    if exm:
        where.append(f"COALESCE(NULLIF(a.maturity,''), p.maturity, '') NOT IN ({','.join('?' * len(exm))})"); params += exm
    if kind:
        where.append("a.kind=?"); params.append(kind)
    if tag:
        where.append("(',' || a.tags || ',') LIKE ?"); params.append(f"%,{tag.lower()},%")
    if project:
        where.append("p.name=? COLLATE NOCASE"); params.append(project)
    ex = _aud(exclude_audience)
    if ex:
        where.append(f"COALESCE(p.audience,'') NOT IN ({','.join('?' * len(ex))})"); params += ex
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY a.updated_at DESC LIMIT ?"
    params.append(limit)
    return {"assets": q(sql, params)}


@mcp.tool()
def log_session(project: str = "", summary: str = "", decisions: str = "", resources: str = "", dead_ends: str = "",
                next_steps: str = "", machine: str = "", session_id: str = "", path: str = "", tags: str = "") -> dict:
    """Record what a working session did. Call at the end of meaningful work. summary is required;
    the rest are free text. resources: tools, APIs, models, other projects used."""
    if not summary:
        return {"error": "summary is required"}
    body = summary
    for label, val in [("Decisions", decisions), ("Resources used", resources), ("Dead ends", dead_ends), ("Next steps", next_steps)]:
        if val:
            body += f"\n\n## {label}\n{val}"
    title = summary.strip().splitlines()[0][:120]
    n = store.add_note("session", title, body, project=project or None, tags=tags, machine=machine or config.MACHINE,
                       session_id=session_id, path=path)
    S.embed_in_background()
    return dict(n)


@mcp.tool(name="add_note")
def add_note_tool(kind: str = "note", title: str = "", body: str = "", project: str = "", tags: str = "", machine: str = "") -> dict:
    """Add a note. kind: decision|howto|resource|issue|idea|note. howto notes are what future sessions
    get from howto(); decisions explain why something is the way it is."""
    if not title:
        return {"error": "title is required"}
    n = store.add_note(kind, title, body, project=project or None, tags=tags, machine=machine or config.MACHINE)
    S.embed_in_background()
    return dict(n)


@mcp.tool()
def link_items(from_project: str = "", to_project: str = "", relation: str = "uses", note: str = "",
               from_asset: str = "", to_asset: str = "") -> dict:
    """Link two things. relation: uses|could-reuse|supersedes|derived-from|shares-code-with|deploys-to|related.
    Give project names, or asset names (from_asset/to_asset) instead."""
    def resolve(project, asset):
        if asset:
            a = one("SELECT id FROM asset WHERE name=? COLLATE NOCASE", (asset,))
            return ("asset", a["id"]) if a else (None, None)
        if project:
            p = get_project(project) or upsert_project(project)
            return ("project", p["id"])
        return (None, None)
    fk, fid = resolve(from_project, from_asset)
    tk, tid = resolve(to_project, to_asset)
    if not fk or not tk:
        return {"error": "could not resolve both ends"}
    add_link(fk, fid, tk, tid, relation, note)
    return {"ok": True, "from": [fk, fid], "to": [tk, tid], "relation": relation}


@mcp.tool()
def rate(name: str = "", maturity: str = "", note: str = "", asset_kind: str = "") -> dict:
    """Rate a project (or an asset, by giving asset_kind) so future sessions know what is usable and what is not.
    maturity: authoritative (the one to use) | usable | experimental | antiquated (superseded, prefer newer) |
    sunset (being retired) | broken | junk (reference only). note: why, and what to use instead.
    Empty maturity returns the vocabulary and current ratings."""
    if not maturity:
        return {"vocabulary": MATURITY,
                "rated_projects": q("SELECT name, maturity, maturity_note FROM project WHERE maturity!='' ORDER BY maturity, name"),
                "rated_assets": q("SELECT name, kind, maturity, maturity_note FROM asset WHERE maturity!='' ORDER BY maturity, name")}
    if maturity not in MATURITY:
        return {"error": f"maturity must be one of {list(MATURITY)}", "meanings": MATURITY}
    if not name:
        return {"error": "name is required"}
    if asset_kind:
        a = one("SELECT * FROM asset WHERE name=? COLLATE NOCASE AND kind=?", (name, asset_kind))
        if not a:
            return {"error": f"no asset {name!r} of kind {asset_kind!r}"}
        a = upsert_asset(a["name"], a["kind"], maturity=maturity, maturity_note=note)
        S.embed_in_background()
        return {"asset": dict(a)}
    if not get_project(name):
        return {"error": f"no project {name!r}; use update_project to create it first"}
    p = upsert_project(name, maturity=maturity, maturity_note=note)
    S.embed_in_background()
    return {"project": dict(p)}


@mcp.tool()
def verify(name: str = "", asset_kind: str = "", note: str = "") -> dict:
    """Record that you ran this project (or asset, with asset_kind) today and it worked. Restarts its trust
    freshness clock (half-life 180 days from verification). note: what you checked. Use after actually
    exercising the code, not after reading it."""
    if not name:
        return {"error": "name is required"}
    return T.verify(name, asset_kind, note)


HANDOFF_STAGES = ("candidate", "shadow", "verified", "promoted", "retired", "rolled-back")
HANDOFF_CLASSES = ("library", "service", "agent-facing", "pipeline", "data-store")


@mcp.tool()
def handoff(name: str = "", stage: str = "", old: str = "", new: str = "", promotion_class: str = "", dependants: str = "",
            report: str = "", note: str = "", project: str = "") -> dict:
    """Record or advance a handoff. One record per name; each call appends a
    dated stage entry. stage: candidate|shadow|verified|promoted|retired|rolled-back. old/new: asset or
    project names being replaced/replacing. promotion_class: library|service|agent-facing|pipeline|data-store.
    report: path to the parity report. note: what happened, failures included. Empty name lists handoffs."""
    if not name:
        return list_handoffs()
    if stage and stage not in HANDOFF_STAGES:
        return {"error": f"stage must be one of {HANDOFF_STAGES}"}
    if promotion_class and promotion_class not in HANDOFF_CLASSES:
        return {"error": f"promotion_class must be one of {HANDOFF_CLASSES}"}
    existing = one("SELECT * FROM note WHERE kind='handoff' AND title=?", (name,))
    head = {}
    body_lines = []
    if existing:
        first, _, rest = existing["body"].partition("\n---\n")
        try:
            head = json.loads(first)
        except ValueError:
            head = {}
        body_lines = rest.splitlines() if rest else []
    for k, v in (("old", old), ("new", new), ("class", promotion_class), ("dependants", dependants), ("report", report)):
        if v:
            head[k] = v
    if stage:
        head["stage"] = stage
        head.setdefault("history", []).append({"stage": stage, "at": now()[:16]})
    entry = f"{now()[:16]} [{stage or head.get('stage', '?')}] {note}".rstrip()
    if note or stage:
        body_lines.append(entry)
    body = json.dumps(head) + "\n---\n" + "\n".join(body_lines)
    tags = "handoff," + ("stage:" + head.get("stage", "candidate")) + (",class:" + head["class"] if head.get("class") else "")
    with tx() as c:
        if existing:
            c.execute("UPDATE note SET body=?, tags=?, project_id=COALESCE((SELECT id FROM project WHERE name=? COLLATE NOCASE), project_id) WHERE id=?",
                      (body, tags, project, existing["id"]))
            pname = one("SELECT name FROM project WHERE id=(SELECT project_id FROM note WHERE id=?)", (existing["id"],))
            index_item_note = existing["id"]
        else:
            pid = (get_project(project) or {}).get("id") if project else None
            c.execute("INSERT INTO note(project_id, kind, title, body, tags, machine, created_at) VALUES (?,?,?,?,?,?,?)",
                      (pid, "handoff", name, body, tags, config.MACHINE, now()))
            index_item_note = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        from .db import index_item
        pn = one("SELECT name FROM project WHERE id=(SELECT project_id FROM note WHERE id=?)", (index_item_note,))
        index_item(c, "note", index_item_note, f"[handoff] {name}", body, tags, pn["name"] if pn else "")
    S.embed_in_background()
    return {"name": name, **head, "log": body_lines[-5:]}


@mcp.tool()
def list_handoffs(stage: str = "") -> dict:
    """Handoffs in flight (or at a given stage), newest activity first. Each shows old, new, class, stage, last entry."""
    rows = q("SELECT n.id, n.title, n.body, n.tags, n.created_at, p.name AS project FROM note n LEFT JOIN project p ON p.id=n.project_id WHERE n.kind='handoff' ORDER BY n.id DESC")
    out = []
    for r in rows:
        first, _, rest = r["body"].partition("\n---\n")
        try:
            head = json.loads(first)
        except ValueError:
            head = {}
        if stage and head.get("stage") != stage:
            continue
        out.append({"name": r["title"], "project": r["project"], **{k: head.get(k) for k in ("stage", "old", "new", "class", "dependants", "report")},
                    "last": rest.splitlines()[-1] if rest.strip() else ""})
    return {"count": len(out), "handoffs": out}


@mcp.tool()
def howto(topic: str = "") -> dict:
    """How-to knowledge: publishing to the git server, adding a machine, using codemem, and whatever docs are ingested.
    Searches howto notes first, then ingested docs. Empty topic lists available howtos."""
    if not topic.strip():
        return {"howtos": q("SELECT id, title, tags FROM note WHERE kind='howto' ORDER BY title")}
    r = S.search(topic, ["note", "doc"], None, 6)
    hits = [x["record"] for x in r["results"] if x["kind"] == "doc" or x["record"].get("kind") == "howto"]
    for h in hits:
        if "source" in h:
            d = one("SELECT body FROM doc WHERE id=?", (h["id"],))
            h["body"] = d["body"][:6000] if d else ""
        elif "id" in h:
            n = one("SELECT body FROM note WHERE id=?", (h["id"],))
            h["body"] = n["body"] if n else h.get("body", "")
    return {"mode": r["mode"], "results": hits}


@mcp.tool()
def activity(days: int = 7, project: str = "", machine: str = "", exclude_audience: str = "", limit: int = 50) -> dict:
    """What happened recently: commits pushed and sessions logged, across all machines."""
    ex = _aud(exclude_audience) or []
    exsql = f" AND p.audience NOT IN ({','.join('?' * len(ex))})" if ex else ""
    since = f"-{max(1, days)} days"
    cparams = [since] + ([project] if project else []) + ex + [limit]
    commits = q(f'''SELECT p.name AS project, c.hash, c.author, c.date, substr(c.message,1,160) AS message, c.pushed_from, c.pushed_at
                    FROM "commit" c JOIN project p ON p.id=c.project_id
                    WHERE c.date >= datetime('now', ?) {"AND p.name=? COLLATE NOCASE" if project else ""} {exsql}
                    ORDER BY c.date DESC LIMIT ?''', cparams)
    nparams = [since] + ([project] if project else []) + ([machine] if machine else []) + ex + [limit]
    notes = q(f'''SELECT p.name AS project, n.kind, n.title, n.machine, n.created_at, substr(n.body,1,300) AS body
                  FROM note n LEFT JOIN project p ON p.id=n.project_id
                  WHERE n.created_at >= datetime('now', ?) {"AND p.name=? COLLATE NOCASE" if project else ""}
                  {"AND n.machine=?" if machine else ""} {exsql.replace("p.audience", "COALESCE(p.audience,'')")}
                  ORDER BY n.created_at DESC LIMIT ?''', nparams)
    return {"days": days, "commits": commits, "notes": notes}


@mcp.tool()
def add_scan_root(path: str = "", machine: str = "", note: str = "", enabled: bool = True) -> dict:
    """Register a directory to be scanned for projects on a machine. Remote machines run
    client/codemem_agent.py which reads its roots from here. Use when a directory is organised enough."""
    if not path:
        return {"error": "path is required"}
    return dict(upsert_scan_root(machine or config.MACHINE, path, note, int(enabled)))


@mcp.tool()
def scan_path(path: str = "") -> dict:
    """Scan a directory on the SERVER machine now and record the projects found. For other machines
    use codemem_agent.py. Empty path scans this machine's registered roots."""
    if path:
        return SC.ingest_scan(SC.scan_payload([path]))
    return SC.scan_local()


@mcp.tool()
def stats() -> dict:
    """Counts of everything, machines seen, index/embedding coverage, server config."""
    return {
        "projects": one("SELECT count(*) AS n FROM project")["n"],
        "locations": one("SELECT count(*) AS n FROM location")["n"],
        "assets": one("SELECT count(*) AS n FROM asset")["n"],
        "notes": one("SELECT count(*) AS n FROM note")["n"],
        "commits": one('SELECT count(*) AS n FROM "commit"')["n"],
        "docs": one("SELECT count(*) AS n FROM doc")["n"],
        "links": one("SELECT count(*) AS n FROM link")["n"],
        "machines": [r["machine"] for r in q("SELECT DISTINCT machine FROM location UNION SELECT DISTINCT machine FROM note WHERE machine!=''")],
        "scan_roots": q("SELECT machine, path, enabled, last_scanned FROM scan_root ORDER BY machine, path"),
        "audiences": q("SELECT audience, count(*) AS n FROM project GROUP BY audience"),
        "origins": q("SELECT origin, count(*) AS n FROM project GROUP BY origin"),
        "maturity": q("SELECT COALESCE(NULLIF(maturity,''),'unrated') AS maturity, count(*) AS n FROM project GROUP BY 1 ORDER BY n DESC"),
        "maturity_vocabulary": MATURITY,
        "trust": q("SELECT CASE WHEN trust IS NULL THEN 'unscored' WHEN trust>=70 THEN 'high (70+)' WHEN trust>=40 THEN 'medium (40-69)' ELSE 'low (<40)' END AS band, count(*) AS n FROM project WHERE origin='own' GROUP BY 1 ORDER BY 1"),
        "index_rows": one("SELECT count(*) AS n FROM search_index")["n"],
        "embedded_rows": one("SELECT count(*) AS n FROM embedding")["n"],
        "embeddings": "on" if config.EMBED_ENABLED else "off",
        "db": str(config.DB_PATH), "machine": config.MACHINE, "port": config.PORT,
    }


# ---- HTTP: ingest, hooks, JSON API, web UI -----------------------------------

async def _json(request: Request):
    try:
        return await request.json()
    except Exception:
        body = (await request.body()).decode()
        return json.loads(body) if body else {}


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request):
    return JSONResponse({"ok": True, "service": "codemem", "machine": config.MACHINE, "time": now()})


@mcp.custom_route("/ingest/push", methods=["POST"])
async def ingest_push(request: Request):
    """Called by the git server's post-receive hook. Body: {repo, ref, old, new, who, from}."""
    d = await _json(request)
    r = gitsync.ingest_push(d.get("repo", ""), d.get("ref", ""), d.get("old", ""), d.get("new", ""),
                            d.get("who", ""), d.get("from", ""))
    S.embed_in_background()
    return JSONResponse(r, status_code=400 if "error" in r else 200)


@mcp.custom_route("/ingest/scan", methods=["POST"])
async def ingest_scan(request: Request):
    """Remote agent posts a scan payload (see codemem.scan.scan_payload)."""
    d = await _json(request)
    r = SC.ingest_scan(d)
    S.embed_in_background()
    return JSONResponse(r)


@mcp.custom_route("/scan_roots", methods=["GET"])
async def scan_roots(request: Request):
    m = request.query_params.get("machine", "")
    rows = q("SELECT path FROM scan_root WHERE machine=? AND enabled=1", (m,)) if m else q("SELECT machine, path FROM scan_root WHERE enabled=1")
    return JSONResponse({"machine": m, "roots": rows})


@mcp.custom_route("/brief", methods=["GET"])
async def brief_route(request: Request):
    """SessionStart hook: text brief for a working directory. 204 if the path is unknown."""
    path = request.query_params.get("path", "")
    machine = request.query_params.get("machine", config.MACHINE)
    p = _project_from_path(path, machine)
    if not p:
        return PlainTextResponse("", status_code=204)
    return PlainTextResponse(brief_text(brief(p)))


@mcp.custom_route("/session/end", methods=["POST"])
async def session_end(request: Request):
    """SessionEnd hook: automatic session record. Body: {machine, path, session_id, first_prompt, last_reply, turns, duration_min}."""
    d = await _json(request)
    machine = d.get("machine") or config.MACHINE
    p = _project_from_path(d.get("path", ""), machine)
    first = (d.get("first_prompt") or "").strip()
    last = (d.get("last_reply") or "").strip()
    if not first and not last:
        return JSONResponse({"skipped": "empty"})
    if d.get("session_id") and one("SELECT id FROM note WHERE session_id=? AND kind='session-auto'", (d["session_id"],)):
        return JSONResponse({"skipped": "duplicate"})
    title = (first.splitlines()[0] if first else last.splitlines()[0])[:120]
    body = f"Asked: {first[:1500]}\n\nOutcome: {last[:3000]}\n\n(turns: {d.get('turns', '?')}, minutes: {d.get('duration_min', '?')}, cwd: {d.get('path', '')})"
    n = store.add_note("session-auto", title, body, project=p["name"] if p else None, machine=machine,
                       session_id=d.get("session_id", ""), path=d.get("path", ""))
    S.embed_in_background()
    return JSONResponse({"ok": True, "note_id": n["id"], "project": p["name"] if p else None})


@mcp.custom_route("/api/search", methods=["GET"])
async def api_search(request: Request):
    qp = request.query_params
    ks = [k for k in qp.get("kinds", "").split(",") if k] or None
    return JSONResponse(S.search(qp.get("q", ""), ks, qp.get("project") or None, int(qp.get("limit", 20)),
                                 exclude_audience=_aud(qp.get("exclude_audience", "")),
                                 exclude_maturity=_aud(qp.get("exclude_maturity", "")),
                                 include_vendor=qp.get("include_vendor") == "1") if qp.get("q") else {"results": []})


@mcp.custom_route("/api/projects", methods=["GET"])
async def api_projects(request: Request):
    qp = request.query_params
    return JSONResponse(list_projects(qp.get("status", ""), qp.get("tag", ""), qp.get("machine", ""), qp.get("audience", ""),
                                      qp.get("exclude_audience", ""), qp.get("maturity", ""), qp.get("exclude_maturity", ""), 1000,
                                      qp.get("origin", "own")))


@mcp.custom_route("/api/project/{name}", methods=["GET"])
async def api_project(request: Request):
    p = get_project(request.path_params["name"])
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    b = brief(p, limit_notes=50, limit_commits=100)
    b["locations"] = q("SELECT * FROM location WHERE project_id=?", (p["id"],))
    return JSONResponse(b)


@mcp.custom_route("/api/project/{name}", methods=["PATCH"])
async def api_project_patch(request: Request):
    d = await _json(request)
    allowed = {k: d[k] for k in ("description", "purpose", "status", "tags", "audience", "github_url", "maturity", "maturity_note", "origin", "visibility") if k in d}
    if allowed.get("maturity") and allowed["maturity"] not in MATURITY:
        return JSONResponse({"error": f"maturity must be one of {list(MATURITY)}"}, status_code=400)
    p = upsert_project(request.path_params["name"], **allowed)
    S.embed_in_background()
    return JSONResponse(dict(p))


@mcp.custom_route("/api/verify", methods=["POST"])
async def api_verify(request: Request):
    d = await _json(request)
    return JSONResponse(T.verify(d.get("name", ""), d.get("asset_kind", ""), d.get("note", "")))


@mcp.custom_route("/api/asset/{id}", methods=["PATCH"])
async def api_asset_patch(request: Request):
    d = await _json(request)
    a = one("SELECT * FROM asset WHERE id=?", (int(request.path_params["id"]),))
    if not a:
        return JSONResponse({"error": "not found"}, status_code=404)
    if d.get("maturity") and d["maturity"] not in MATURITY:
        return JSONResponse({"error": f"maturity must be one of {list(MATURITY)}"}, status_code=400)
    allowed = {k: d[k] for k in ("description", "usage", "tags", "maturity", "maturity_note", "path") if k in d}
    a = upsert_asset(a["name"], a["kind"], **allowed)
    S.embed_in_background()
    return JSONResponse(dict(a))


@mcp.custom_route("/api/assets", methods=["GET"])
async def api_assets(request: Request):
    qp = request.query_params
    return JSONResponse(find_assets(qp.get("q", ""), qp.get("kind", ""), qp.get("tag", ""), qp.get("project", ""),
                                    qp.get("exclude_audience", ""), qp.get("exclude_maturity", ""), 500, qp.get("imports", "")))


@mcp.custom_route("/api/notes", methods=["GET"])
async def api_notes(request: Request):
    qp = request.query_params
    where, params = [], []
    if qp.get("kind"):
        where.append("n.kind=?"); params.append(qp["kind"])
    if qp.get("project"):
        where.append("p.name=? COLLATE NOCASE"); params.append(qp["project"])
    ex = _aud(qp.get("exclude_audience", ""))
    if ex:
        where.append(f"COALESCE(p.audience,'') NOT IN ({','.join('?' * len(ex))})"); params += ex
    sql = "SELECT n.*, p.name AS project FROM note n LEFT JOIN project p ON p.id=n.project_id"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY n.created_at DESC LIMIT ?"
    params.append(int(qp.get("limit", 200)))
    return JSONResponse({"notes": q(sql, params)})


@mcp.custom_route("/api/activity", methods=["GET"])
async def api_activity(request: Request):
    qp = request.query_params
    return JSONResponse(activity(int(qp.get("days", 14)), qp.get("project", ""), qp.get("machine", ""),
                                 qp.get("exclude_audience", ""), int(qp.get("limit", 100))))


@mcp.custom_route("/api/docs", methods=["GET"])
async def api_docs(request: Request):
    did = request.query_params.get("id")
    if did:
        d = one("SELECT * FROM doc WHERE id=?", (int(did),))
        return JSONResponse(d or {"error": "not found"}, status_code=200 if d else 404)
    return JSONResponse({"docs": q("SELECT id, source, title, updated_at, length(body) AS size FROM doc ORDER BY title")})


@mcp.custom_route("/api/stats", methods=["GET"])
async def api_stats(request: Request):
    return JSONResponse(stats())


@mcp.custom_route("/web/{name}", methods=["GET"])
async def web_static(request: Request):
    """Vendored front-end files (marked.min.js). No path traversal: name must be a plain file in web/."""
    from starlette.responses import FileResponse
    name = request.path_params["name"]
    f = WEB / name
    if "/" in name or ".." in name or not f.is_file():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(str(f))


@mcp.custom_route("/", methods=["GET"])
async def index(request: Request):
    return HTMLResponse((WEB / "index.html").read_text())


def run():
    connect()
    print(f"codemem on http://{config.HOST}:{config.PORT}  (mcp at /mcp, web UI at /)  db={config.DB_PATH}")
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    run()

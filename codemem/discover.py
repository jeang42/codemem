"""Asset discovery on the server, and ingestion of assets posted by remote agents.

Server side: mine every own repo (bare repo in GIT_ROOT, else the local working copy) with the
shared rules in discover_core. Remote side: client/codemem_agent.py runs the same rules on its
machine and posts assets inside its scan payload; ingest_assets() below stores them identically.

Then link retooling across ALL projects and machines from what is stored: identical blob hashes,
and Python files whose symbol sets overlap by half or more, become `shares-code-with` links.
"""
import json, urllib.request
from collections import defaultdict
from pathlib import Path
from . import config
from .db import q, one, tx
from .store import upsert_asset, add_link, get_project
from .discover_core import scan_bare, scan_worktree, asset_name

DESCRIBE_MODEL = "qwen3-coder:30b"


def draft_description(path, text_head):
    prompt = ("One sentence, max 25 words, plain: what does this file do and when would someone reuse it? "
              "If unclear, start with 'Unclear:'. Answer JSON {\"description\": \"...\"}\n\nFile: " + path + "\n\n" + text_head[:4000])
    try:
        req = urllib.request.Request(f"{config.OLLAMA_URL}/api/chat", data=json.dumps({
            "model": DESCRIBE_MODEL, "stream": False, "format": "json", "options": {"temperature": 0.2, "num_ctx": 8192},
            "messages": [{"role": "user", "content": prompt}]}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return (json.loads(json.load(r)["message"]["content"]).get("description") or "").strip()[:300]
    except Exception:
        return ""


def ingest_assets(project, assets, machine="", base_path="", describe=True):
    """Store asset dicts (from scan_bare/scan_worktree or a remote payload) for a project.
    Existing assets at the same path are refreshed; human descriptions are never overwritten."""
    created = updated = drafted = 0
    for a in assets:
        path = a["path"].replace("\\", "/")
        existing = one("SELECT * FROM asset WHERE project_id=? AND (path=? OR path LIKE ?)", (project["id"], path, f"%/{path}"))
        fields = {"last_changed": a.get("last_changed"), "change_count": a.get("change_count"), "blob_hash": a.get("blob_hash") or "",
                  "size": a.get("size"), "symbols": ",".join(a.get("symbols") or [])[:2000],
                  "imports": ",".join(a.get("imports") or [])[:1000], "func_hashes": json.dumps(a.get("func_hashes") or [])[:20000],
                  "signatures": "\n".join(a.get("signatures") or [])[:6000]}
        if existing:
            if not existing["usage"] and a.get("usage"):
                fields["usage"] = a["usage"]
            if base_path and not existing["machine"]:
                fields["machine"] = machine; fields["path"] = f"{base_path}/{path}"
            upsert_asset(existing["name"], existing["kind"], **fields)
            updated += 1
        else:
            desc = a.get("description") or ""
            tags = "auto-discovered"
            if not desc and describe and a.get("head"):
                desc = draft_description(path, a["head"])
                if desc:
                    tags += ",auto-described"; drafted += 1
            upsert_asset(asset_name(path, project["name"]), a["kind"], project=project["name"],
                         path=f"{base_path}/{path}" if base_path else path, machine=machine,
                         description=desc or f"Unclear: no docstring in {path}", usage=a.get("usage") or "", tags=tags, **fields)
            created += 1
    return {"created": created, "updated": updated, "drafted": drafted}


def repo_for_project(p):
    bare = config.GIT_ROOT / f"{p['name']}.git"
    if bare.is_dir():
        return bare, True
    loc = one("SELECT path FROM location WHERE project_id=? AND machine=? AND is_git=1", (p["id"], config.MACHINE))
    if loc and Path(loc["path"], ".git").exists():
        return Path(loc["path"]), False
    return None, None


def discover(names=None, describe=True, log=print):
    projects = q("SELECT * FROM project WHERE origin='own'" + (f" AND name IN ({','.join('?' * len(names))})" if names else ""), names or ())
    tot = defaultdict(int)
    for p in projects:
        repo, bare = repo_for_project(p)
        if not repo:
            continue
        local = one("SELECT path FROM location WHERE project_id=? AND machine=?", (p["id"], config.MACHINE))
        assets = list(scan_bare(repo) if bare else scan_worktree(repo))
        if describe:
            _attach_heads(repo, bare, assets)
        r = ingest_assets(p, assets, machine=config.MACHINE if local else "", base_path=local["path"] if local else "", describe=describe)
        for k, v in r.items():
            tot[k] += v
        log(f"  {p['name']}: {len(assets)} candidates")
    tot["links"] = link_shared_code(log)
    tot["projects"] = len(projects)
    return dict(tot)


def _attach_heads(repo, bare, assets):
    """For files without a description, fetch the first 60 lines so the model can draft one."""
    from .discover_core import _git
    for a in assets:
        if a.get("description"):
            continue
        if bare:
            raw = _git(repo, "cat-file", "-p", a["blob_hash"], binary=True)
        else:
            try:
                raw = (Path(repo) / a["path"]).read_bytes()
            except OSError:
                raw = b""
        a["head"] = "\n".join(raw.decode("utf-8", "replace").splitlines()[:60])


def link_shared_code(log=print):
    """Cross-project, cross-machine: identical blobs and >=50% overlapping symbol sets."""
    rows = q("""SELECT a.path, a.blob_hash, a.symbols, a.func_hashes, p.name AS project FROM asset a JOIN project p ON p.id=a.project_id
                WHERE p.origin='own' AND a.tags LIKE '%auto-discovered%'""")
    pairs = defaultdict(set)
    byblob = defaultdict(list)
    for r in rows:
        if r["blob_hash"]:
            byblob[r["blob_hash"]].append(r)
    for locs in byblob.values():
        projs = sorted({r["project"] for r in locs})
        for i, a in enumerate(projs):
            for b in projs[i + 1:]:
                fa = next(r["path"] for r in locs if r["project"] == a); fb = next(r["path"] for r in locs if r["project"] == b)
                pairs[(a, b)].add(f"identical: {Path(fa).name} = {Path(fb).name}" if Path(fa).name == Path(fb).name else f"identical: {fa} = {fb}")
    syms = [(r["project"], r["path"], frozenset(r["symbols"].split(","))) for r in rows if r["symbols"] and r["symbols"].count(",") >= 4]
    for i, (pa, fa, sa) in enumerate(syms):
        for pb, fb, sb in syms[i + 1:]:
            if pa == pb:
                continue
            j = len(sa & sb) / len(sa | sb)
            if j >= 0.5:
                a, b = sorted([pa, pb])
                pairs[(a, b)].add(f"similar ({j:.0%} same functions): {fa} ~ {fb}")
    # function level: the same normalized body in two different projects (renames and docstrings ignored)
    byfunc = defaultdict(list)
    for r in rows:
        if not r["func_hashes"]:
            continue
        try:
            for f in json.loads(r["func_hashes"]):
                if f.get("lines", 0) >= 8:
                    byfunc[f["hash"]].append((r["project"], r["path"], f["name"], f["lines"]))
        except ValueError:
            pass
    for hits in byfunc.values():
        projs = sorted({h[0] for h in hits})
        if len(projs) < 2:
            continue
        for i, a in enumerate(projs):
            for b in projs[i + 1:]:
                ha = next(h for h in hits if h[0] == a); hb = next(h for h in hits if h[0] == b)
                if any(n.startswith("identical:") and Path(ha[1]).name in n for n in pairs[(a, b)]):
                    continue  # whole file already reported
                pairs[(a, b)].add(f"function {ha[2]} ({ha[3]} lines): {ha[1]} = {hb[1]}" + (f" as {hb[2]}" if hb[2] != ha[2] else ""))
    with tx() as c:
        c.execute("DELETE FROM link WHERE relation='shares-code-with' AND (note LIKE 'identical:%' OR note LIKE 'similar (%' OR note LIKE 'function %')")
    n = 0
    for (a, b), files in pairs.items():
        pa, pb = get_project(a), get_project(b)
        if pa and pb:
            add_link("project", pa["id"], "project", pb["id"], "shares-code-with", "; ".join(sorted(files))[:1000])
            n += 1
    log(f"  shared code: {n} project pairs linked")
    return n

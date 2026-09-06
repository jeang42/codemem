"""Ingest the git server: every bare repo in GIT_ROOT, the push log, and Gitea's metadata.

Two entry points:
  backfill()                 walk every repo, ingest all commits not yet stored (idempotent)
  ingest_push(repo, ref, old, new, who, from_ip)   called by the post-receive hook via /ingest/push
"""
import json, subprocess, urllib.request
from pathlib import Path
from . import config
from .db import q, one, tx, now, index_item
from .store import upsert_project, get_project

ZERO = "0" * 40
FMT = "%H%x1f%an%x1f%aI%x1f%B%x1e"


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=300)


def repo_names():
    return sorted(p.name[:-4] for p in config.GIT_ROOT.glob("*.git") if p.is_dir())


def _log_commits(repo, *range_args):
    """Two passes: metadata (record-separated) and files (--name-only), joined by hash."""
    meta = _git(repo, "log", f"--format={FMT}", "--date-order", *range_args)
    if meta.returncode != 0:
        raise RuntimeError(meta.stderr.strip()[:300])
    files = _git(repo, "log", "--format=%x1e%H", "--name-only", *range_args)
    fmap = {}
    for rec in files.stdout.split("\x1e"):
        lines = [l for l in rec.split("\n") if l.strip()]
        if lines:
            fmap[lines[0].strip()] = lines[1:]
    out = []
    for rec in meta.stdout.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) < 4 or not parts[0].strip():
            continue
        h = parts[0].strip()
        out.append({"hash": h, "author": parts[1], "date": parts[2], "message": parts[3].strip(),
                    "files": fmap.get(h, [])})
    return out


def _store_commits(project_id, pname, commits, ref="", pushed=None):
    pushed = pushed or {}
    added = 0
    with tx() as c:
        for cm in commits:
            cur = c.execute('INSERT OR IGNORE INTO "commit"(project_id, hash, author, date, message, files, ref, pushed_at, pushed_by, pushed_from) VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (project_id, cm["hash"], cm["author"], cm["date"], cm["message"],
                             "\n".join(cm["files"][:200]), ref, pushed.get("at"), pushed.get("by"), pushed.get("from")))
            if cur.rowcount:
                added += 1
                cid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                subject = cm["message"].splitlines()[0] if cm["message"] else ""
                index_item(c, "commit", cid, f"{pname} {cm['hash'][:8]}: {subject}",
                           cm["message"] + "\nfiles: " + " ".join(cm["files"][:50]), "", pname)
    _refresh_stats(project_id)
    return added


def _refresh_stats(project_id):
    with tx() as c:
        c.execute("""UPDATE project SET commit_count=(SELECT count(*) FROM "commit" WHERE project_id=?),
                     first_commit=(SELECT min(date) FROM "commit" WHERE project_id=?),
                     last_commit=(SELECT max(date) FROM "commit" WHERE project_id=?) WHERE id=?""",
                  (project_id, project_id, project_id, project_id))


def _project_for_repo(name):
    repo = config.GIT_ROOT / f"{name}.git"
    desc = ""
    try:
        d = (repo / "description").read_text().strip()
        if d and not d.startswith("Unnamed repository"):
            desc = d
    except OSError:
        pass
    remote = f"ssh://git@localhost{config.GIT_ROOT}/{name}.git"
    return upsert_project(name, description=desc, remote_url=remote)


def backfill(names=None, log=print):
    total = 0
    for name in names or repo_names():
        repo = config.GIT_ROOT / f"{name}.git"
        p = _project_for_repo(name)
        try:
            commits = _log_commits(repo, "--all")
        except RuntimeError as e:
            log(f"  {name}: git log failed: {e}")
            continue
        added = _store_commits(p["id"], p["name"], commits)
        total += added
        log(f"  {name}: {len(commits)} commits, {added} new")
    apply_push_log()
    return total


def ingest_push(repo, ref, old, new, who="", from_ip=""):
    """Called by the post-receive hook. Returns the number of commits added."""
    name = repo[:-4] if repo.endswith(".git") else repo
    path = config.GIT_ROOT / f"{name}.git"
    if not path.is_dir():
        return {"error": f"no such repo {name}"}
    p = _project_for_repo(name)
    if new == ZERO:
        return {"repo": name, "ref": ref, "deleted": True, "added": 0}
    rng = new if old == ZERO else f"{old}..{new}"
    try:
        commits = _log_commits(path, rng)
    except RuntimeError as e:
        return {"error": str(e)}
    added = _store_commits(p["id"], p["name"], commits, ref=ref, pushed={"at": now(), "by": who, "from": from_ip})
    return {"repo": name, "ref": ref, "commits": len(commits), "added": added}


def apply_push_log():
    """Attach who/where/when from the git host's push.log to commits that lack it (backfill only)."""
    if not config.PUSH_LOG.exists():
        return 0
    n = 0
    with tx() as c:
        for line in config.PUSH_LOG.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) < 8:
                continue
            ts, who, frm, repo, ref, action, count, rng = parts[:8]
            if action != "update" or ".." not in rng:
                continue
            old, new = rng.split("..")
            p = one("SELECT id FROM project WHERE name=?", (repo,))
            if not p:
                continue
            path = config.GIT_ROOT / f"{repo}.git"
            r = _git(path, "rev-list", f"{old}..{new}")
            if r.returncode != 0:
                continue
            for h in r.stdout.split():
                cur = c.execute('UPDATE "commit" SET pushed_at=?, pushed_by=?, pushed_from=?, ref=? WHERE project_id=? AND hash=? AND pushed_at IS NULL',
                                (ts, who, frm, ref, p["id"], h))
                n += cur.rowcount
    return n


# ---- Gitea -------------------------------------------------------------------

def _gitea_token():
    try:
        return config.GITEA_TOKEN_FILE.read_text().strip()
    except OSError:
        return ""


def gitea_sync(log=print):
    """Pull descriptions, topics and web URLs from Gitea for repos it mirrors."""
    tok = _gitea_token()
    if not tok:
        log("  gitea: no token readable, skipping")
        return 0
    page, n = 1, 0
    while True:
        req = urllib.request.Request(f"{config.GITEA_URL}/api/v1/user/repos?limit=50&page={page}",
                                     headers={"Authorization": f"token {tok}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                repos = json.load(r)
        except Exception as e:
            log(f"  gitea: {e}")
            return n
        if not repos:
            break
        for rp in repos:
            name = rp["name"]
            url = f"{config.GITEA_PUBLIC_URL}/{rp['owner']['login']}/{name}"
            topics = ",".join(rp.get("topics") or [])
            p = get_project(name)
            existing_tags = (p or {}).get("tags") or ""
            tags = ",".join(t for t in [existing_tags, topics] if t)
            upsert_project(name, description=rp.get("description") or "", gitea_url=url, tags=tags)
            n += 1
        page += 1
    log(f"  gitea: {n} repos synced")
    return n

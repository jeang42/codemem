"""Trust: a computed 0-100 score for every project and asset, with the breakdown kept beside it.

Trust is NOT maturity. Maturity is a human judgment and wins: authoritative floors trust at 85,
junk caps it at 15. Trust fills the gap for everything unrated, from evidence the repos already
give: how recently it changed (or was verified), how much work went into it, hygiene, whether it
is deployed and running, whether other code reuses it, whether something supersedes it, and the
grade from an external code review table where one exists.

Recomputed on every sync. `verify(name)` records verified_at, which restarts the freshness clock
(half-life 180 days from verification instead of 365 from the last commit).
"""
import json, math, re, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from . import config
from .db import q, one, tx, now

REVIEW_TABLE = Path("/srv/git/PROJECT_REVIEW.md")   # optional: markdown table with a grade column
GRADE = {"A": 100, "B": 80, "C": 60, "D": 40, "F": 20}
CAP = {"junk": 15, "broken": 25, "sunset": 35, "antiquated": 45}
FLOOR = {"authoritative": 85, "usable": 60}
STATUS_CAP = {"abandoned": 30, "archived": 35}
BAD_NAME = re.compile(r"(\.bad|_orig|_old|\bjunk\b|\btest\b|scratch)", re.I)


def _days_since(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 86400)
    except ValueError:
        return None


def freshness(last_change, verified_at):
    """0-100. Half-life 365 d from the last change, 180 d from a verification; the better of the two."""
    scores = []
    d = _days_since(last_change)
    if d is not None:
        scores.append(100 * 0.5 ** (d / 365))
    d = _days_since(verified_at)
    if d is not None:
        scores.append(100 * 0.5 ** (d / 180))
    return max(scores) if scores else None


def review_grades():
    grades = {}
    if REVIEW_TABLE.exists():
        for m in re.finditer(r"^\|[^|]*\|\s*`([^`]+)`\s*\|[^|]*\|[^|]*\|\s*([A-F])\s*\|", REVIEW_TABLE.read_text(), re.M):
            grades[m.group(1)] = GRADE[m.group(2)]
    return grades


def active_units():
    units = set()
    for scope in (["--user"], []):
        try:
            out = subprocess.run(["systemctl", *scope, "list-units", "--type=service", "--type=timer", "--state=active", "--no-legend"],
                                 capture_output=True, text=True, timeout=20).stdout
            units |= {l.split()[0] for l in out.splitlines() if l.strip()}
        except Exception:
            pass
    return units


def weighted(parts):
    """parts: {name: (score or None, weight)} -> (0-100, breakdown dict). Missing signals drop out."""
    tot = sum(w for s, w in parts.values() if s is not None)
    if not tot:
        return None, {}
    score = sum(s * w for s, w in parts.values() if s is not None) / tot
    return score, {k: (round(s) if s is not None else None) for k, (s, w) in parts.items()}


def clamp(score, maturity, status=""):
    if score is None:
        return None
    if maturity in CAP:
        score = min(score, CAP[maturity])
    if maturity in FLOOR:
        score = max(score, FLOOR[maturity])
    if status in STATUS_CAP:
        score = min(score, STATUS_CAP[status])
    return int(round(max(0, min(100, score))))


def compute_projects(grades, units, log=print):
    n = 0
    for p in q("SELECT * FROM project"):
        locs = q("SELECT * FROM location WHERE project_id=?", (p["id"],))
        last = max([p["last_commit"] or ""] + [l["last_local_commit"] or "" for l in locs]) or None
        fresh = freshness(last, p["verified_at"])
        commits = p["commit_count"] or 0
        activity = 100 * (1 - math.exp(-commits / 30)) if (commits or locs) else None
        key = ",".join(l["key_files"] or "" for l in locs)
        hyg = None
        if locs or p["description"]:
            hyg = 0
            if "README.md" in key or (p["description"] and not p["description"].startswith("Unclear")): hyg += 30
            if "CLAUDE.md" in key: hyg += 20
            if "requirements.txt" in key or "pyproject.toml" in key or "package.json" in key: hyg += 20
            if "Dockerfile" in key or "compose" in key or "run.sh" in key: hyg += 15
            if "install.sh" in key or ".env.example" in key: hyg += 15
            if BAD_NAME.search(p["name"]): hyg = 0
            hyg = min(100, hyg)
        svc = q("SELECT name, path FROM asset WHERE project_id=? AND kind IN ('service','mcp-server')", (p["id"],))
        deployed = None
        if svc:
            deployed = 100 if any(Path(a["path"]).name in units for a in svc if a["path"]) else 0
        incoming = one("""SELECT count(*) n FROM link WHERE (to_kind='project' AND to_id=? AND relation IN ('uses','could-reuse','derived-from','shares-code-with'))
                          OR (from_kind='project' AND from_id=? AND relation='shares-code-with')""", (p["id"], p["id"]))["n"]
        reuse = min(100, 35 * incoming)
        superseded = one("SELECT count(*) n FROM link WHERE to_kind='project' AND to_id=? AND relation='supersedes'", (p["id"],))["n"] > 0
        review = grades.get(p["name"])
        score, br = weighted({"freshness": (fresh, 30), "activity": (activity, 15), "hygiene": (hyg, 15),
                              "deployed": (deployed, 15), "reuse": (reuse, 10), "review": (review, 15)})
        if score is not None and superseded:
            score = min(score, 40); br["superseded"] = True
        if p["origin"] == "vendor":
            br["vendor"] = True
        t = clamp(score, p["maturity"], p["status"])
        with tx() as c:
            c.execute("UPDATE project SET trust=?, trust_breakdown=? WHERE id=?", (t, json.dumps(br), p["id"]))
        n += 1
    return n


def compute_assets(units, log=print):
    n = 0
    ptrust = {r["id"]: r["trust"] for r in q("SELECT id, trust FROM project")}
    for a in q("SELECT * FROM asset"):
        fresh = freshness(a["last_changed"] or a["updated_at"], a["verified_at"])
        churn = 100 * (1 - math.exp(-(a["change_count"] or 0) / 8)) if a["change_count"] else None
        tags = a["tags"] or ""
        desc = 0 if (a["description"] or "").startswith("Unclear") else 50 if "auto-described" in tags else 100 if a["description"] else 0
        if a["usage"]:
            desc = min(100, desc + 20)
        deployed = None
        if a["kind"] in ("service", "mcp-server") and a["path"]:
            deployed = 100 if Path(a["path"]).name in units else 0
        human = 100 if "auto-discovered" not in tags else None   # a person chose to register it
        proj = ptrust.get(a["project_id"])
        score, br = weighted({"freshness": (fresh, 30), "churn": (churn, 15), "description": (desc, 10),
                              "deployed": (deployed, 15), "curated": (human, 10), "project": (proj, 25)})
        t = clamp(score, a["maturity"])
        with tx() as c:
            c.execute("UPDATE asset SET trust=?, trust_breakdown=? WHERE id=?", (t, json.dumps(br), a["id"]))
        n += 1
    return n


def compute_all(log=print):
    t0 = time.time()
    grades, units = review_grades(), active_units()
    np_ = compute_projects(grades, units, log)
    na = compute_assets(units, log)
    log(f"  trust: {np_} projects, {na} assets, {len(grades)} review grades, {len(units)} active units, {time.time()-t0:.1f}s")
    return {"projects": np_, "assets": na}


def verify(name, asset_kind="", note=""):
    if asset_kind:
        a = one("SELECT * FROM asset WHERE name=? COLLATE NOCASE AND kind=?", (name, asset_kind))
        if not a:
            return {"error": f"no asset {name!r} of kind {asset_kind!r}"}
        with tx() as c:
            c.execute("UPDATE asset SET verified_at=?, verified_note=? WHERE id=?", (now(), note, a["id"]))
        compute_all(log=lambda *_: None)
        return {"asset": dict(one("SELECT * FROM asset WHERE id=?", (a["id"],)))}
    p = one("SELECT * FROM project WHERE name=? COLLATE NOCASE", (name,))
    if not p:
        return {"error": f"no project {name!r}"}
    with tx() as c:
        c.execute("UPDATE project SET verified_at=?, verified_note=? WHERE id=?", (now(), note, p["id"]))
    compute_all(log=lambda *_: None)
    return {"project": dict(one("SELECT * FROM project WHERE id=?", (p["id"],)))}

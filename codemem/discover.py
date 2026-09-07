"""Asset discovery: mine every own repo for reusable pieces and for code shared between repos.

Works from the bare repos in GIT_ROOT (whole history is there, no working copy needed), falling
back to the local working copy for projects that exist only on disk. For each candidate file it
records an asset tagged `auto-discovered` with: kind (guessed), description (module docstring or
header comment, else a model draft tagged `auto-described`), usage (from argparse flags / imports),
last change date, change count, blob hash and size. Existing assets at the same project+path are
updated, never duplicated, and a human-written description is never overwritten.

Then it finds retooling: identical blobs and near-identical Python function sets across different
repos become `shares-code-with` links between the projects, naming the files.
"""
import ast, json, re, subprocess, urllib.request
from collections import defaultdict
from pathlib import Path
from . import config
from .db import q, one, tx, now
from .store import upsert_asset, add_link, get_project

DIR_HINTS = ("tools/", "scripts/", "bin/", "hooks/", "prompts/", "skills/", "utils/", "utilities/", "lib/", "helpers/")
SKIP_PARTS = ("test", "tests/", "__pycache__", "node_modules", "venv", ".venv", "site-packages", "migrations/",
              "vendor/", "third_party", "dist/", "build/", "static/", "assets/", "examples/", "example")
SKIP_NAMES = {"__init__.py", "setup.py", "conftest.py", "manage.py", "wsgi.py", "asgi.py"}
MIN_SIZE, MAX_SIZE = 400, 400_000
DESCRIBE_MODEL = "qwen3-coder:30b"


def _git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=300)
    return r.stdout if r.returncode == 0 else ""


def _blob(repo, sha):
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-p", sha], capture_output=True, timeout=60).stdout


def head_tree(repo):
    """[(path, blob_sha, size)] at HEAD."""
    out = []
    for line in _git(repo, "ls-tree", "-r", "-l", "HEAD").splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 4 and parts[1] == "blob":
            out.append((path, parts[2], int(parts[3]) if parts[3] != "-" else 0))
    return out


def file_history(repo):
    """path -> (last_date, change_count) in one pass."""
    hist = {}
    date = None
    for line in _git(repo, "log", "--format=%x00%aI", "--name-only", "--date-order").splitlines():
        if line.startswith("\x00"):
            date = line[1:]
        elif line.strip() and date:
            d, n = hist.get(line, (None, 0))
            hist[line] = (d or date, n + 1)  # first seen in date order = most recent
    return hist


def is_candidate(path, size):
    low = path.lower()
    if size < MIN_SIZE or size > MAX_SIZE:
        return False
    if any(s in low for s in SKIP_PARTS) or Path(path).name in SKIP_NAMES:
        return False
    name = Path(path).name
    ext = Path(path).suffix.lower()
    if name in ("Dockerfile", "docker-compose.yml", "compose.yml", "SKILL.md") or ext in (".service", ".timer"):
        return True
    if ext in (".py", ".sh", ".ps1"):
        return True
    if ext == ".md" and (low.startswith("prompts/") or "/prompts/" in low or low.startswith("skills/") or "/skills/" in low):
        return True
    return False


def analyse(path, text):
    """Guess kind, pull description/usage/function names from file content."""
    ext = Path(path).suffix.lower()
    name = Path(path).name
    low = path.lower()
    desc, usage, funcs, kind = "", "", [], None
    if ext == ".py":
        try:
            tree = ast.parse(text)
            desc = (ast.get_docstring(tree) or "").strip()
            funcs = sorted({n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))})
        except SyntaxError:
            pass
        has_main = "__main__" in text or "argparse" in text or "click" in text
        if "FastMCP" in text or "mcp.server" in text:
            kind = "mcp-server"
        elif has_main:
            kind = "script"
            flags = re.findall(r'add_argument\(\s*["\'](--[\w-]+)', text)[:8]
            usage = f"python {path} " + " ".join(flags) if flags else f"python {path}"
        elif len(funcs) >= 3 and (path.count("/") <= 1 or any(h in low for h in DIR_HINTS)):
            # internal package modules (src/api/routes/x.py) are not reusable pieces; top-level and tools/ ones are
            kind = "module"
            usage = f"from {Path(path).with_suffix('').as_posix().replace('/', '.')} import {', '.join(funcs[:4])}"
        else:
            return None
    elif ext in (".sh", ".ps1"):
        lines = text.splitlines()
        comment = []
        for l in lines[1:25]:
            if l.startswith("#") and not l.startswith("#!"):
                comment.append(l.lstrip("# ").rstrip())
            elif comment:
                break
        desc = "\n".join(comment).strip()
        kind = "hook" if "hooks/" in low else "script"
        usage = f"{'bash' if ext == '.sh' else 'pwsh'} {path}"
    elif name == "Dockerfile" or name in ("docker-compose.yml", "compose.yml"):
        kind = "docker"
        desc = "\n".join(l.lstrip("# ") for l in text.splitlines()[:15] if l.startswith("#")).strip()
        usage = f"docker compose -f {path} up -d" if "compose" in name else f"docker build -f {path} ."
    elif ext in (".service", ".timer"):
        kind = "service"
        m = re.search(r"^Description=(.*)$", text, re.M)
        desc = m.group(1).strip() if m else ""
        usage = f"systemctl enable --now {name}"
    elif ext == ".md":
        kind = "skill" if name == "SKILL.md" else "prompt"
        desc = " ".join(l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#"))[:300]
    if not kind:
        return None
    if desc:
        desc = desc.split("\n\n")[0].strip()[:600]
    return {"kind": kind, "description": desc, "usage": usage, "funcs": funcs}


def draft_description(path, text):
    prompt = ("One sentence, max 25 words, plain: what does this file do and when would someone reuse it? "
              "If unclear, start with 'Unclear:'. Answer JSON {\"description\": \"...\"}\n\nFile: " + path +
              "\n\n" + "\n".join(text.splitlines()[:60])[:4000])
    try:
        req = urllib.request.Request(f"{config.OLLAMA_URL}/api/chat", data=json.dumps({
            "model": DESCRIBE_MODEL, "stream": False, "format": "json", "options": {"temperature": 0.2, "num_ctx": 8192},
            "messages": [{"role": "user", "content": prompt}]}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return (json.loads(json.load(r)["message"]["content"]).get("description") or "").strip()[:300]
    except Exception:
        return ""


def repo_for_project(p):
    bare = config.GIT_ROOT / f"{p['name']}.git"
    if bare.is_dir() and _git(bare, "rev-parse", "HEAD").strip():
        return bare, None
    loc = one("SELECT path FROM location WHERE project_id=? AND machine=? AND is_git=1", (p["id"], config.MACHINE))
    if loc and Path(loc["path"], ".git").exists():
        return Path(loc["path"]), loc["path"]
    return None, None


def discover(names=None, describe=True, log=print):
    projects = q("SELECT * FROM project WHERE origin='own'" + (f" AND name IN ({','.join('?' * len(names))})" if names else ""), names or ())
    blobs = defaultdict(set)          # blob sha -> {(project, path)}
    funcsets = []                     # (project, path, frozenset(funcs))
    created = updated = drafted = 0
    for p in projects:
        repo, local = repo_for_project(p)
        if not repo:
            continue
        tree = head_tree(repo)
        hist = file_history(repo)
        local_abs = one("SELECT path FROM location WHERE project_id=? AND machine=?", (p["id"], config.MACHINE))
        n = 0
        for path, sha, size in tree:
            if not is_candidate(path, size):
                continue
            raw = _blob(repo, sha)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            info = analyse(path, text)
            if not info:
                continue
            blobs[sha].add((p["name"], path))
            if len(info["funcs"]) >= 5:
                funcsets.append((p["name"], path, frozenset(info["funcs"])))
            existing = one("SELECT * FROM asset WHERE project_id=? AND (path=? OR path LIKE ?)", (p["id"], path, f"%/{path}"))
            last, cnt = hist.get(path, (None, 0))
            fields = {"last_changed": last, "change_count": cnt, "blob_hash": sha, "size": size}
            if existing:
                tags = existing["tags"] or ""
                if existing["kind"] == info["kind"] and not existing["usage"]:
                    fields["usage"] = info["usage"]
                upsert_asset(existing["name"], existing["kind"], **fields)
                updated += 1
            else:
                desc = info["description"]
                tags = "auto-discovered"
                if not desc and describe:
                    desc = draft_description(path, text)
                    if desc:
                        tags += ",auto-described"; drafted += 1
                abs_path = f"{local_abs['path']}/{path}" if local_abs else path
                upsert_asset(f"{Path(path).name} ({p['name']})", info["kind"], project=p["name"], path=abs_path,
                             machine=config.MACHINE if local_abs else "", description=desc or f"Unclear: no docstring in {path}",
                             usage=info["usage"], tags=tags, **fields)
                created += 1
            n += 1
        log(f"  {p['name']}: {n} candidates")
    links = link_shared_code(blobs, funcsets, log)
    return {"projects": len(projects), "created": created, "updated": updated, "drafted": drafted, "links": links}


def link_shared_code(blobs, funcsets, log=print):
    pairs = defaultdict(set)   # (projA, projB) -> {"fileA = fileB"}
    for sha, locs in blobs.items():
        projs = sorted({pr for pr, _ in locs})
        if len(projs) < 2:
            continue
        for i, a in enumerate(projs):
            for b in projs[i + 1:]:
                fa = [pa for pr, pa in locs if pr == a][0]; fb = [pb for pr, pb in locs if pr == b][0]
                pairs[(a, b)].add(f"identical: {fa} = {fb}")
    for i, (pa, fa, sa) in enumerate(funcsets):
        for pb, fb, sb in funcsets[i + 1:]:
            if pa == pb:
                continue
            j = len(sa & sb) / len(sa | sb)
            if j >= 0.5:
                a, b = sorted([pa, pb])
                pairs[(a, b)].add(f"similar ({j:.0%} same functions): {fa} ~ {fb}")
    n = 0
    for (a, b), files in pairs.items():
        pa, pb = get_project(a), get_project(b)
        if pa and pb:
            add_link("project", pa["id"], "project", pb["id"], "shares-code-with", "; ".join(sorted(files))[:1000])
            n += 1
    log(f"  shared code: {n} project pairs linked")
    return n

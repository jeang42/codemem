"""Pure discovery logic shared by the server (codemem/discover.py) and the remote agent
(client/codemem_agent.py, which imports this file as codemem_discover). Stdlib only, no DB.

Given a project directory (or a bare repo), yields asset dicts:
  path, kind, description, usage, symbols, last_changed, change_count, blob_hash, size
"""
import ast, hashlib, os, re, subprocess
from pathlib import Path

DIR_HINTS = ("tools/", "scripts/", "bin/", "hooks/", "prompts/", "skills/", "utils/", "utilities/", "lib/", "helpers/")
SKIP_PARTS = ("test", "tests/", "__pycache__", "node_modules", "venv", ".venv", "-env/", "_env/", "env/bin/", "site-packages",
              "migrations/", "vendor/", "third_party", "dist/", "build/", "static/", "assets/", "examples/", "example",
              "activate", "/lib/python", ".git/")
SKIP_NAMES = {"__init__.py", "setup.py", "conftest.py", "manage.py", "wsgi.py", "asgi.py"}
MIN_SIZE, MAX_SIZE = 400, 400_000


def is_candidate(path, size):
    low = path.replace("\\", "/").lower()
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
    """Guess kind; pull description, usage and symbol names. None if not worth recording."""
    path = path.replace("\\", "/")
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
        if "FastMCP" in text or "mcp.server" in text or "MCPServer" in text:
            kind = "mcp-server"
        elif has_main:
            kind = "script"
            flags = re.findall(r'add_argument\(\s*["\'](--[\w-]+)', text)[:8]
            usage = f"python {path} " + " ".join(flags) if flags else f"python {path}"
        elif len(funcs) >= 3 and (path.count("/") <= 1 or any(h in low for h in DIR_HINTS)):
            kind = "module"
            usage = f"from {Path(path).with_suffix('').as_posix().replace('/', '.')} import {', '.join(funcs[:4])}"
        else:
            return None
    elif ext in (".sh", ".ps1"):
        comment = []
        for l in text.splitlines()[1:25]:
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
    return {"kind": kind, "description": desc, "usage": usage, "symbols": funcs}


def asset_name(path, project):
    """'tools/server.py (myproject)': keep the parent dir so generic names stay unique within a repo."""
    pp = Path(path.replace("\\", "/"))
    short = f"{pp.parent.name}/{pp.name}" if pp.parent.name else pp.name
    return f"{short} ({project})"


def _git(repo, *args, binary=False):
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=not binary, timeout=300)
        return r.stdout if r.returncode == 0 else ("" if not binary else b"")
    except Exception:
        return "" if not binary else b""


def git_history(repo):
    """path -> (last_date, change_count) in one log pass."""
    hist, date = {}, None
    for line in _git(repo, "log", "--format=%x00%aI", "--name-only", "--date-order").splitlines():
        if line.startswith("\x00"):
            date = line[1:]
        elif line.strip() and date:
            d, n = hist.get(line, (None, 0))
            hist[line] = (d or date, n + 1)
    return hist


def blob_sha(data):
    """Same hash git uses for a blob, so local and bare-repo scans agree."""
    h = hashlib.sha1()
    h.update(f"blob {len(data)}\x00".encode()); h.update(data)
    return h.hexdigest()


def scan_bare(repo):
    """Assets at HEAD of a bare repo: yields asset dicts with repo-relative paths."""
    hist = git_history(repo)
    for line in _git(repo, "ls-tree", "-r", "-l", "HEAD").splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) < 4 or parts[1] != "blob":
            continue
        sha, size = parts[2], int(parts[3]) if parts[3] != "-" else 0
        if not is_candidate(path, size):
            continue
        raw = _git(repo, "cat-file", "-p", sha, binary=True)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        info = analyse(path, text)
        if not info:
            continue
        last, cnt = hist.get(path, (None, 0))
        yield {"path": path, "last_changed": last, "change_count": cnt, "blob_hash": sha, "size": size, **info}


def scan_worktree(root, max_files=20000):
    """Assets in a working directory (git or not): yields asset dicts with root-relative paths."""
    root = Path(root)
    is_git = (root / ".git").exists()
    hist = git_history(root) if is_git else {}
    if is_git:
        files = [f for f in _git(root, "ls-files", "-z").split("\0") if f]
    else:
        files = []
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if not d.startswith(".") and d not in ("node_modules", "venv", ".venv", "__pycache__")]
            for f in fn:
                files.append(os.path.relpath(os.path.join(dp, f), root).replace("\\", "/"))
            if len(files) > max_files:
                break
    for rel in files:
        fp = root / rel
        try:
            size = fp.stat().st_size
        except OSError:
            continue
        if not is_candidate(rel, size):
            continue
        try:
            raw = fp.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        info = analyse(rel, text)
        if not info:
            continue
        last, cnt = hist.get(rel, (None, 0))
        if not last:
            try:
                import datetime
                last = datetime.datetime.fromtimestamp(fp.stat().st_mtime).astimezone().isoformat(timespec="seconds")
            except OSError:
                pass
        yield {"path": rel, "last_changed": last, "change_count": cnt, "blob_hash": blob_sha(raw), "size": size, **info}

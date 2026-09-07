"""Small local project scanner. Answers "what lives at this path" and records it as a location.

Deliberately modest: This finds project directories under
a root (git repo or a recognised marker), reads the README head and CLAUDE.md head, counts
languages by extension, notes key files, and records the git remote/branch/dirty state.

scan_payload() produces the same JSON the remote agent (client/codemem_agent.py) posts to
/ingest/scan, so both machines go through one importer: ingest_scan().
"""
import os, subprocess
from collections import Counter
from pathlib import Path
import re
from . import config
from .db import now, one, q, tx
# package-manager caches that look like projects: com.unity.burst@f7a407abf4d5, org.foo.bar@1.2.3
VENDOR_NAME = re.compile(r"^(com|org|net|io)\.[a-z0-9-]+\..+@[0-9a-f.]+$", re.I)
from .store import upsert_project, upsert_location, project_name_from_remote, get_project, is_vendor_remote, is_local_remote, is_excluded

MARKERS = {"pyproject.toml", "setup.py", "requirements.txt", "package.json", "Cargo.toml", "go.mod",
           "CMakeLists.txt", "Makefile", "docker-compose.yml", "compose.yml", "Dockerfile", "CLAUDE.md",
           "project.godot", "pom.xml", "build.gradle", "*.csproj", "*.sln"}
KEY_FILES = ["CLAUDE.md", "README.md", "Dockerfile", "docker-compose.yml", "compose.yml", "requirements.txt",
             "pyproject.toml", "package.json", "Makefile", "install.sh", "run.sh", ".env.example"]
LANG = {".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
        ".sh": "shell", ".rs": "rust", ".go": "go", ".gd": "gdscript", ".cs": "csharp",
        ".java": "java", ".c": "c", ".cpp": "cpp", ".h": "c", ".html": "html", ".css": "css", ".sql": "sql",
        ".ps1": "powershell", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".lua": "lua", ".rb": "ruby",
        ".php": "php", ".swift": "swift", ".kt": "kotlin", ".ipynb": "notebook"}


def _git(path, *args):
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def is_project_dir(p: Path):
    if (p / ".git").exists():
        return True
    for m in MARKERS:
        if "*" in m:
            if any(p.glob(m)):
                return True
        elif (p / m).exists():
            return True
    return False


def find_projects(root: Path, max_depth=config.SCAN_MAX_DEPTH):
    """Yield project directories under root. Does not descend into a project once found."""
    root = Path(root).expanduser()
    if not root.is_dir():
        return
    stack = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            kids = sorted(x for x in d.iterdir() if x.is_dir() and not x.is_symlink() and x.name not in config.SKIP_DIRS
                          and not x.name.startswith("."))
        except OSError:
            continue
        for k in kids:
            if is_project_dir(k):
                yield k
            elif depth + 1 < max_depth:
                stack.append((k, depth + 1))


def _head(path: Path, n=12):
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    out = []
    for l in lines:
        if l.strip():
            out.append(l.strip())
        if len(out) >= n:
            break
    return "\n".join(out)[:1500]


def describe(p: Path):
    """Gather facts about one project directory. Pure: no DB access."""
    langs, files = Counter(), 0
    for dirpath, dirnames, filenames in os.walk(p):
        dirnames[:] = [d for d in dirnames if d not in config.SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            files += 1
            ext = os.path.splitext(f)[1].lower()
            if ext in LANG:
                langs[LANG[ext]] += 1
        if files > 20000:
            break
    is_git = (p / ".git").exists()
    remote = _git(p, "remote", "get-url", "origin") if is_git else ""
    return {
        "path": str(p), "name": p.name, "is_git": is_git, "remote_url": remote,
        "branch": _git(p, "rev-parse", "--abbrev-ref", "HEAD") if is_git else "",
        "dirty": bool(_git(p, "status", "--porcelain")) if is_git else False,
        "last_local_commit": _git(p, "log", "-1", "--format=%aI") if is_git else "",
        "file_count": files,
        "languages": ",".join(f"{k}:{v}" for k, v in langs.most_common(6)),
        "key_files": ",".join(k for k in KEY_FILES if (p / k).exists()),
        "readme_head": _head(p / "README.md") or _head(p / "CLAUDE.md"),
        "claude_md_head": _head(p / "CLAUDE.md", 8),
    }


def scan_payload(roots, machine=config.MACHINE):
    projects = []
    for root in roots:
        for d in find_projects(Path(root)):
            projects.append(describe(d))
    return {"machine": machine, "roots": [str(r) for r in roots], "scanned_at": now(), "projects": projects}


def ingest_scan(payload):
    """Import a scan payload (local or posted by a remote agent). Returns counts."""
    machine = payload.get("machine") or "unknown"
    created = updated = excluded = 0
    asset_stats = {}
    for d in payload.get("projects", []):
        remote = d.get("remote_url") or ""
        if is_local_remote(remote):
            remote = ""   # a clone of a local path (backup mirror, L:/..., file://) says nothing about the project's home
        name = project_name_from_remote(remote) or d["name"]
        if is_excluded(name, d.get("path"), remote):
            excluded += 1
            continue   # excluded names never enter, whatever machine posts it
        existed = get_project(name) is not None
        primary_langs = ",".join(x.split(":")[0] for x in (d.get("languages") or "").split(",") if x)
        fields = {"languages": primary_langs}
        if remote.startswith("https://github.com"):
            fields["github_url"] = remote.removesuffix(".git")
        elif remote:
            fields["remote_url"] = remote
        if is_vendor_remote(remote) or VENDOR_NAME.match(d["name"]):
            fields["origin"] = "vendor"  # someone else's repo cloned here; never treated as our own work
        if not existed and d.get("readme_head"):
            # first line of the README that is not a heading, badge, or the bare project name
            for line in d["readme_head"].splitlines():
                t = line.strip()
                if t and not t.startswith(("#", "!", "[", "<", "=", "-")) and t.lower() != d["name"].lower():
                    fields["description"] = t[:200]
                    break
        p = upsert_project(name, **fields)
        upsert_location(p["id"], machine, d["path"], is_git=int(bool(d.get("is_git"))),
                        remote_url=d.get("remote_url") or "", branch=d.get("branch") or "",
                        dirty=int(bool(d.get("dirty"))), last_local_commit=d.get("last_local_commit") or None,
                        file_count=d.get("file_count"), languages=d.get("languages") or "",
                        key_files=d.get("key_files") or "", readme_head=d.get("readme_head") or "")
        created += 0 if existed else 1
        updated += 1 if existed else 0
        if d.get("assets") and p["origin"] != "vendor":
            from .discover import ingest_assets
            r = ingest_assets(p, d["assets"], machine=machine, base_path=d["path"].replace("\\", "/").rstrip("/"), describe=True)
            asset_stats = {k: asset_stats.get(k, 0) + v for k, v in r.items()}
    with tx() as c:
        for r in payload.get("roots", []):
            c.execute("""INSERT INTO scan_root(machine, path, last_scanned) VALUES (?,?,?)
                         ON CONFLICT(machine, path) DO UPDATE SET last_scanned=excluded.last_scanned""",
                      (machine, r, now()))
    out = {"machine": machine, "projects": len(payload.get("projects", [])), "created": created, "updated": updated, "excluded": excluded}
    if asset_stats:
        from .discover import link_shared_code
        from .trust import compute_all
        out["assets"] = asset_stats
        out["links"] = link_shared_code(log=lambda *_: None)
        compute_all(log=lambda *_: None)
    return out


def scan_local(roots=None, machine=config.MACHINE):
    if roots is None:
        rows = q("SELECT path FROM scan_root WHERE machine=? AND enabled=1", (machine,))
        roots = [r["path"] for r in rows] or config.DEFAULT_SCAN_ROOTS
    return ingest_scan(scan_payload(roots, machine))

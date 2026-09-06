#!/usr/bin/env python3
"""Remote scan agent for machines other than the server. Stdlib only.

Reads this machine's scan roots from the server (or takes them on the command line), scans them
the same way the server does, and posts the result to /ingest/scan.

    python3 codemem_agent.py                # roots registered for this machine via add_scan_root
    python3 codemem_agent.py ~/Coding ~/src # explicit roots (also registers them)

Config: CODEMEM_URL, CODEMEM_MACHINE. Nothing is scanned until a root is registered.
"""
import json, os, socket, subprocess, sys, time, urllib.parse, urllib.request
from collections import Counter
from pathlib import Path

URL = os.environ.get("CODEMEM_URL", "http://localhost:8055").rstrip("/")
MACHINE = os.environ.get("CODEMEM_MACHINE", socket.gethostname().split(".")[0])
SKIP = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", ".cache", "dist", "build", ".next",
        "site-packages", ".tox", "target", ".idea", ".vscode", "checkpoints", "models", "outputs", "output", "data"}
MARKERS = ["pyproject.toml", "setup.py", "requirements.txt", "package.json", "Cargo.toml", "go.mod", "CMakeLists.txt",
           "Makefile", "docker-compose.yml", "compose.yml", "Dockerfile", "CLAUDE.md", "project.godot", "pom.xml", "build.gradle"]
KEY = ["CLAUDE.md", "README.md", "Dockerfile", "docker-compose.yml", "compose.yml", "requirements.txt", "pyproject.toml",
       "package.json", "Makefile", "install.sh", "run.sh", ".env.example"]
LANG = {".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript", ".sh": "shell", ".rs": "rust", ".go": "go",
        ".gd": "gdscript", ".cs": "csharp", ".java": "java", ".c": "c", ".cpp": "cpp", ".html": "html", ".css": "css",
        ".sql": "sql", ".ps1": "powershell", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".swift": "swift", ".kt": "kotlin"}


def git(p, *a):
    try:
        r = subprocess.run(["git", "-C", str(p), *a], capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def is_project(p):
    return (p / ".git").exists() or any((p / m).exists() for m in MARKERS) or any(p.glob("*.csproj")) or any(p.glob("*.sln"))


def find(root, depth=3):
    root = Path(root).expanduser()
    stack = [(root, 0)]
    while stack:
        d, n = stack.pop()
        try:
            kids = [x for x in d.iterdir() if x.is_dir() and not x.is_symlink() and x.name not in SKIP and not x.name.startswith(".")]
        except OSError:
            continue
        for k in sorted(kids):
            if is_project(k):
                yield k
            elif n + 1 < depth:
                stack.append((k, n + 1))


def head(p, n=12):
    try:
        lines = [l.strip() for l in p.read_text(errors="replace").splitlines() if l.strip()]
        return "\n".join(lines[:n])[:1500]
    except OSError:
        return ""


def describe(p):
    langs, files = Counter(), 0
    for dp, dn, fn in os.walk(p):
        dn[:] = [d for d in dn if d not in SKIP and not d.startswith(".")]
        for f in fn:
            files += 1
            e = os.path.splitext(f)[1].lower()
            if e in LANG:
                langs[LANG[e]] += 1
        if files > 20000:
            break
    g = (p / ".git").exists()
    return {"path": str(p), "name": p.name, "is_git": g, "remote_url": git(p, "remote", "get-url", "origin") if g else "",
            "branch": git(p, "rev-parse", "--abbrev-ref", "HEAD") if g else "", "dirty": bool(git(p, "status", "--porcelain")) if g else False,
            "last_local_commit": git(p, "log", "-1", "--format=%aI") if g else "", "file_count": files,
            "languages": ",".join(f"{k}:{v}" for k, v in langs.most_common(6)), "key_files": ",".join(k for k in KEY if (p / k).exists()),
            "readme_head": head(p / "README.md") or head(p / "CLAUDE.md")}


def main():
    roots = [str(Path(r).expanduser()) for r in sys.argv[1:]]
    if not roots:
        with urllib.request.urlopen(f"{URL}/scan_roots?machine={urllib.parse.quote(MACHINE)}", timeout=10) as r:
            roots = [x["path"] for x in json.load(r)["roots"]]
    if not roots:
        print(f"no scan roots registered for {MACHINE}; pass directories or use add_scan_root"); return
    projects = [describe(d) for root in roots for d in find(root)]
    payload = {"machine": MACHINE, "roots": roots, "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "projects": projects}
    req = urllib.request.Request(f"{URL}/ingest/scan", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        print(json.load(r))


if __name__ == "__main__":
    main()

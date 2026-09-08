"""All tunables in one place. Every value can be overridden by an environment variable."""
import os, socket
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CODEMEM_DATA", Path.home() / ".codemem"))
DB_PATH = Path(os.environ.get("CODEMEM_DB", DATA_DIR / "codemem.db"))
BACKUP_DIR = Path(os.environ.get("CODEMEM_BACKUP_DIR", DATA_DIR / "backups"))
HOST = os.environ.get("CODEMEM_HOST", "0.0.0.0")
PORT = int(os.environ.get("CODEMEM_PORT", "8055"))
MACHINE = os.environ.get("CODEMEM_MACHINE", socket.gethostname())

# The git host: a directory of bare repos served over SSH. codemem reads them directly, so it
# normally runs on the same machine. GIT_SSH_HOST is what clients put in their remote URLs
# (user@host) and is used to recognise our own remotes; empty means "same host, current user".
GIT_ROOT = Path(os.environ.get("GIT_ROOT", "/srv/git"))
GIT_SSH_HOST = os.environ.get("CODEMEM_GIT_SSH_HOST", socket.gethostname())
PUSH_LOG = GIT_ROOT / "logs" / "push.log"
GITEA_URL = os.environ.get("GITEA_URL", "http://127.0.0.1:3000").rstrip("/")
GITEA_PUBLIC_URL = os.environ.get("GITEA_PUBLIC_URL", GITEA_URL).rstrip("/")
GITEA_TOKEN_FILE = Path(os.environ.get("GITEA_TOKEN_FILE", GIT_ROOT / ".gitea-token"))

# GitHub/GitLab owners that are YOU (comma list). A clone whose remote owner is not listed here is
# vendor code. Empty (the default) disables owner-based vendor detection; mark vendor clones by hand.
OWN_REMOTE_OWNERS = {o.strip().lower() for o in os.environ.get("CODEMEM_OWN_OWNERS", "").split(",") if o.strip()}
# Names/paths that must never enter codemem at all. A regex matched case-insensitively against project
# names, remotes and scan paths, e.g. r"(^|[/\\_\-\s])(secret-project|other)([/\\_\-\s.]|$)" for whole
# path segments or name tokens. Empty (the default) excludes nothing. Purge existing rows with
# `codemem purge <project>`.
EXCLUDE_PATTERN = os.environ.get("CODEMEM_EXCLUDE", "")

# Default audience for new projects. "unrestricted" = personal work, no content filtering applied.
DEFAULT_AUDIENCE = os.environ.get("CODEMEM_DEFAULT_AUDIENCE", "unrestricted")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
EMBED_MODEL = os.environ.get("CODEMEM_EMBED_MODEL", "nomic-embed-text")
EMBED_ENABLED = os.environ.get("CODEMEM_EMBED", "1") not in ("0", "false", "no")
# Local model used to draft descriptions and run the targeted code review.
DESCRIBE_MODEL = os.environ.get("CODEMEM_DESCRIBE_MODEL", "qwen3-coder:30b")

# Optional markdown table of code-review grades: rows like `| ... | `project` | ... | ... | B |`.
# Feeds the trust score's review signal. Empty = no review signal.
REVIEW_TABLE = os.environ.get("CODEMEM_REVIEW_TABLE", "")

# Markdown that gets ingested as knowledge. Colon-separated globs, re-hashed on every run.
# Add the docs of your other infrastructure repos here so howto() can answer from them.
DOC_SOURCES = [s for s in os.environ.get("CODEMEM_DOC_SOURCES", ":".join([
    str(HERE / "README.md"),
    str(HERE / "docs" / "*.md"),
])).split(":") if s]

# Default scan roots for THIS machine. Other machines add theirs via add_scan_root.
DEFAULT_SCAN_ROOTS = [s for s in os.environ.get("CODEMEM_SCAN_ROOTS", ":".join([
    str(Path.home() / "projects"),
])).split(":") if s]

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", ".cache", "dist",
             "build", ".next", "site-packages", ".tox", ".mypy_cache",
             "target", ".idea", ".vscode", "checkpoints", "models", "outputs", "output", "data",
             "backup", "backups", "archive", "archives", "old", "_old", "mirror", "mirrors",
             "PackageCache", "Library", "Temp", "Logs", "obj", "bin", ".godot", ".import"}   # Unity/Godot/.NET caches
SCAN_MAX_DEPTH = 3

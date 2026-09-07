"""All tunables in one place. Every value can be overridden by an environment variable."""
import os, socket
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("CODEMEM_DB", str(Path.home() / ".codemem" / "codemem.db")))
HOST = os.environ.get("CODEMEM_HOST", "0.0.0.0")
PORT = int(os.environ.get("CODEMEM_PORT", "8055"))
MACHINE = os.environ.get("CODEMEM_MACHINE", socket.gethostname())

GIT_ROOT = Path(os.environ.get("GIT_ROOT", "/srv/git"))
PUSH_LOG = GIT_ROOT / "logs" / "push.log"
GITEA_URL = os.environ.get("GITEA_URL", "http://127.0.0.1:3000").rstrip("/")
GITEA_PUBLIC_URL = os.environ.get("GITEA_PUBLIC_URL", GITEA_URL).rstrip("/")
GITEA_TOKEN_FILE = Path(os.environ.get("GITEA_TOKEN_FILE", GIT_ROOT / ".gitea-token"))

# GitHub/GitLab owners that are YOU. A clone whose remote owner is not listed here is vendor code.
OWN_REMOTE_OWNERS = {o.strip().lower() for o in os.environ.get("CODEMEM_OWN_OWNERS", "").split(",") if o.strip()}
# Default audience for new projects. "unrestricted" = personal, no content filtering applied.
DEFAULT_AUDIENCE = os.environ.get("CODEMEM_DEFAULT_AUDIENCE", "unrestricted")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
EMBED_MODEL = os.environ.get("CODEMEM_EMBED_MODEL", "nomic-embed-text")
EMBED_ENABLED = os.environ.get("CODEMEM_EMBED", "1") not in ("0", "false", "no")

# Markdown that gets ingested as knowledge. Globs, re-hashed on every run.
DOC_SOURCES = [s for s in os.environ.get("CODEMEM_DOC_SOURCES", ":".join([
    str(HERE / "README.md"),
    str(HERE / "docs" / "*.md"),
])).split(":") if s]

# Default scan roots for THIS machine. Other machines add theirs via add_scan_root.
DEFAULT_SCAN_ROOTS = [s for s in os.environ.get("CODEMEM_SCAN_ROOTS", ":".join([
])).split(":") if s]

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", ".cache", "dist",
             "build", ".next", "site-packages", ".tox", ".mypy_cache",
             "target", ".idea", ".vscode", "checkpoints", "models", "outputs", "output", "data",
             "backup", "backups", "archive", "archives", "old", "_old", "mirror", "mirrors",
             "PackageCache", "Library", "Temp", "Logs", "obj", "bin", ".godot", ".import"}   # Unity/Godot/.NET caches
SCAN_MAX_DEPTH = 3

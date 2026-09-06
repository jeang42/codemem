"""Ingest markdown files as searchable knowledge, and seed the howto notes future sessions need.

The docs come from this repo and any others listed in CODEMEM_DOC_SOURCES. They are re-read and
re-hashed on every run, so editing the source file is all it takes to update the answer.
"""
import glob, hashlib
from pathlib import Path
from . import config
from .db import q, one
from .store import upsert_doc, add_note, upsert_asset


def _title(path: Path, body: str):
    for l in body.splitlines():
        if l.startswith("# "):
            return l[2:].strip()
    return path.stem.replace("_", " ").replace("-", " ")


def ingest_docs(sources=None, log=print):
    changed = total = 0
    for pattern in sources or config.DOC_SOURCES:
        for f in sorted(glob.glob(pattern)):
            p = Path(f)
            try:
                body = p.read_text(errors="replace")
            except OSError:
                continue
            digest = hashlib.sha1(body.encode()).hexdigest()
            title = _title(p, body)
            _, did_change = upsert_doc(str(p), f"{title} ({p.parent.name}/{p.name})", body, digest)
            total += 1
            changed += int(did_change)
    log(f"  docs: {total} files, {changed} changed")
    return changed


SEED_NOTES = [
    ("howto", "Use codemem from Claude Code", """codemem is the MCP server registered as `codemem`. Start of a task: call `search` with what you are about to build and `find_assets` before writing a utility. When you finish something reusable, `register_asset` it with a one-line usage. At the end of a session call `log_session` with what was done, decisions, and dead ends. `howto("publish")` explains the git host once its docs are ingested (CODEMEM_DOC_SOURCES). `project_brief(path)` gives everything known about the current project. The web UI is served at the server's root URL.""",
     "codemem,howto,claude-code"),
]

# (name, kind, project, path, description, usage, tags). Add your own infrastructure scripts here or
# register them with the register_asset tool; this list is only what codemem knows about itself.
SEED_ASSETS = [
    ("codemem MCP server", "mcp-server", "codemem", str(config.HERE),
     "This server. Project/asset/session/commit memory across machines, hybrid search, web UI.",
     "`claude mcp add --transport http --scope user codemem http://<host>:8055/mcp`", "mcp,memory,codemem"),
]


def seed(log=print):
    n = 0
    for kind, title, body, tags in SEED_NOTES:
        if not one("SELECT id FROM note WHERE kind=? AND title=?", (kind, title)):
            add_note(kind, title, body, tags=tags, machine=config.MACHINE)
            n += 1
    for name, kind, project, path, desc, usage, tags in SEED_ASSETS:
        upsert_asset(name, kind, project=project, path=path, machine=config.MACHINE,
                     description=desc, usage=usage, tags=tags)
    log(f"  seed: {n} new howto notes, {len(SEED_ASSETS)} assets asserted")
    return n

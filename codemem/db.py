"""SQLite schema, connection, and the single FTS5 index that spans every record type."""
import json, sqlite3, threading, time
from contextlib import contextmanager
from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS project (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  description TEXT DEFAULT '', purpose TEXT DEFAULT '', status TEXT DEFAULT 'active',
  audience TEXT DEFAULT 'personal',
  tags TEXT DEFAULT '', languages TEXT DEFAULT '',
  remote_url TEXT DEFAULT '', gitea_url TEXT DEFAULT '', github_url TEXT DEFAULT '',
  first_commit TEXT, last_commit TEXT, commit_count INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS location (
  id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES project(id) ON DELETE CASCADE,
  machine TEXT NOT NULL, path TEXT NOT NULL, is_git INTEGER DEFAULT 0,
  remote_url TEXT DEFAULT '', branch TEXT DEFAULT '', dirty INTEGER DEFAULT 0,
  last_local_commit TEXT, file_count INTEGER, languages TEXT DEFAULT '',
  key_files TEXT DEFAULT '', readme_head TEXT DEFAULT '', last_scanned TEXT,
  UNIQUE(machine, path));
CREATE TABLE IF NOT EXISTS asset (
  id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES project(id) ON DELETE SET NULL,
  name TEXT NOT NULL, kind TEXT NOT NULL, path TEXT DEFAULT '', machine TEXT DEFAULT '',
  description TEXT DEFAULT '', usage TEXT DEFAULT '', tags TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT, UNIQUE(name, kind));
CREATE TABLE IF NOT EXISTS note (
  id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES project(id) ON DELETE SET NULL,
  kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT DEFAULT '', tags TEXT DEFAULT '',
  machine TEXT DEFAULT '', session_id TEXT DEFAULT '', path TEXT DEFAULT '', created_at TEXT);
CREATE TABLE IF NOT EXISTS "commit" (
  id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES project(id) ON DELETE CASCADE,
  hash TEXT NOT NULL, author TEXT, date TEXT, message TEXT, files TEXT DEFAULT '',
  ref TEXT DEFAULT '', pushed_at TEXT, pushed_by TEXT, pushed_from TEXT,
  UNIQUE(project_id, hash));
CREATE TABLE IF NOT EXISTS link (
  id INTEGER PRIMARY KEY, from_kind TEXT, from_id INTEGER, to_kind TEXT, to_id INTEGER,
  relation TEXT NOT NULL, note TEXT DEFAULT '', created_at TEXT,
  UNIQUE(from_kind, from_id, to_kind, to_id, relation));
CREATE TABLE IF NOT EXISTS doc (
  id INTEGER PRIMARY KEY, source TEXT NOT NULL UNIQUE, title TEXT, body TEXT, hash TEXT,
  updated_at TEXT);
CREATE TABLE IF NOT EXISTS scan_root (
  id INTEGER PRIMARY KEY, machine TEXT NOT NULL, path TEXT NOT NULL, enabled INTEGER DEFAULT 1,
  note TEXT DEFAULT '', last_scanned TEXT, UNIQUE(machine, path));
CREATE TABLE IF NOT EXISTS embedding (
  kind TEXT NOT NULL, ref_id INTEGER NOT NULL, model TEXT NOT NULL, hash TEXT, vec BLOB,
  PRIMARY KEY(kind, ref_id));
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
  kind UNINDEXED, ref_id UNINDEXED, project UNINDEXED, audience UNINDEXED, title, body, tags,
  tokenize='porter unicode61');
CREATE INDEX IF NOT EXISTS idx_commit_date ON "commit"(date);
CREATE INDEX IF NOT EXISTS idx_commit_project ON "commit"(project_id, date);
CREATE INDEX IF NOT EXISTS idx_note_project ON note(project_id, created_at);
"""

_lock = threading.RLock()
_conn = None


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def connect():
    global _conn
    with _lock:
        if _conn is None:
            config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False, timeout=30)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.executescript(SCHEMA)
        return _conn


@contextmanager
def tx():
    """Serialised write transaction. SQLite is fine with this at our scale."""
    c = connect()
    with _lock:
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def q(sql, params=()):
    with _lock:
        return [dict(r) for r in connect().execute(sql, params).fetchall()]


def one(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


# ---- search index maintenance ------------------------------------------------
# Every record type writes itself into search_index through index_item, so search
# needs no joins and new kinds need no schema change.

def index_item(c, kind, ref_id, title, body, tags="", project=""):
    """Index one record. Audience is inherited from the project so filtering needs no joins."""
    audience = ""
    if project:
        r = c.execute("SELECT audience FROM project WHERE name=? COLLATE NOCASE", (project,)).fetchone()
        audience = (r[0] if r else "") or ""
    c.execute("DELETE FROM search_index WHERE kind=? AND ref_id=?", (kind, ref_id))
    c.execute("INSERT INTO search_index(kind, ref_id, project, audience, title, body, tags) VALUES (?,?,?,?,?,?,?)",
              (kind, ref_id, project or "", audience, title or "", (body or "")[:20000], tags or ""))
    c.execute("DELETE FROM embedding WHERE kind=? AND ref_id=?", (kind, ref_id))


def unindex(c, kind, ref_id):
    c.execute("DELETE FROM search_index WHERE kind=? AND ref_id=?", (kind, ref_id))
    c.execute("DELETE FROM embedding WHERE kind=? AND ref_id=?", (kind, ref_id))


def rebuild_index():
    """Drop and recreate search_index from the source tables."""
    from .store import reindex_all
    reindex_all()


def tags_norm(tags):
    if not tags:
        return ""
    if isinstance(tags, str):
        parts = [t.strip() for t in tags.replace(";", ",").split(",")]
    else:
        parts = [str(t).strip() for t in tags]
    return ",".join(sorted({p.lower() for p in parts if p}))


def dumps(x):
    return json.dumps(x, ensure_ascii=False, default=str)

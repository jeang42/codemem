#!/usr/bin/env bash
# Smoke test: install, import, boot the server against a throwaway database, check it serves.
# Usage: scripts/smoke.sh [python]      (default: the python on PATH)
set -euo pipefail

PY="${1:-python3}"
PORT="${SMOKE_PORT:-8099}"
TMP="$(mktemp -d)"
trap 'kill "${SRV:-}" 2>/dev/null || true; rm -rf "$TMP"' EXIT

echo "== import check =="
"$PY" -c "import codemem, codemem.server, codemem.store, codemem.db, codemem.trust; print('imports ok')"

echo "== cli help =="
"$PY" -m codemem.cli --help > /dev/null && echo "cli ok"

echo "== boot server on :$PORT with a throwaway db =="
CODEMEM_DB="$TMP/smoke.db" \
CODEMEM_BACKUP_DIR="$TMP/backups" \
CODEMEM_HOST=127.0.0.1 \
CODEMEM_PORT="$PORT" \
CODEMEM_EMBED=0 \
GIT_ROOT="$TMP/git" \
"$PY" -m codemem.cli serve > "$TMP/serve.log" 2>&1 &
SRV=$!

for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:$PORT/health" > /dev/null 2>&1; then break; fi
  if ! kill -0 "$SRV" 2>/dev/null; then echo "server died:"; cat "$TMP/serve.log"; exit 1; fi
  sleep 0.5
done

echo "-- health"
curl -sf "http://127.0.0.1:$PORT/health" | grep -q '"ok":true' && echo "health ok"

echo "-- web ui"
curl -sf "http://127.0.0.1:$PORT/" | grep -qi "<!doctype html" && echo "web ui ok"

echo "-- json api"
curl -sf "http://127.0.0.1:$PORT/api/projects" | grep -q '"projects"' && echo "api ok"

echo "-- schema created"
"$PY" - "$TMP/smoke.db" <<'PYEOF'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
need = {"project", "location", "asset", "note", "commit", "link", "doc", "scan_root", "search_index"}
missing = need - have
assert not missing, f"missing tables: {missing}"
print("schema ok")
PYEOF

echo "-- note lifecycle: add, index, delete, deindex"
CODEMEM_DB="$TMP/smoke.db" CODEMEM_EMBED=0 "$PY" - <<'PYEOF'
from codemem.store import add_note, delete_note
from codemem.db import q

n = add_note("note", "smoke note", "a note written by the smoke test", tags="smoke")
nid = n["id"]
assert q("SELECT 1 FROM search_index WHERE kind='note' AND ref_id=?", (nid,)), "note was not indexed"

gone = delete_note(nid)
assert gone and gone["id"] == nid, "delete_note did not return the deleted row"
assert not q("SELECT 1 FROM note WHERE id=?", (nid,)), "note row survived the delete"
assert not q("SELECT 1 FROM search_index WHERE kind='note' AND ref_id=?", (nid,)), "index row survived the delete"
assert delete_note(nid) is None, "deleting a missing note should return None, not raise"
print("note lifecycle ok")
PYEOF

echo "-- malformed-call guard"
CODEMEM_DB="$TMP/smoke.db" CODEMEM_EMBED=0 "$PY" - <<'PYEOF'
import codemem.server as s
from codemem.db import q

before = len(q("SELECT id FROM note"))

# The failure this guards: a client that does not serialise its arguments sends the later ones
# as literal markup inside an earlier one.
bad = s.log_session(summary='Summary.\n<parameter name="decisions">leaked</parameter>')
assert "error" in bad, "malformed log_session was accepted"
assert len(q("SELECT id FROM note")) == before, "malformed call wrote a note anyway"

# A well-formed call is untouched.
ok = s.log_session(summary="smoke session", decisions="a decision")
assert ok.get("id"), "well-formed log_session was rejected"

# A note that QUOTES the markup, with the named field filled, is legitimate and must pass.
quoted = s.log_session(summary='about <parameter name="decisions"> leaking', decisions="filled in")
assert quoted.get("id"), "false positive: a deliberate quote was rejected"

assert "error" in s.add_note_tool(title="t", body='<parameter name="tags">x</parameter>'), "add_note unguarded"
print("malformed-call guard ok")
PYEOF

echo "== smoke passed =="

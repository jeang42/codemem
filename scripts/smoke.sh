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

echo "== smoke passed =="

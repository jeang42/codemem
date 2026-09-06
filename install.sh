#!/usr/bin/env bash
# Deploy codemem on the server: venv, data dir, user-scope systemd units, initial data. Idempotent.
#   ./install.sh            everything (no root needed: user-scope systemd units)
#   ./install.sh --data     only re-run the data steps
# Environment: CODEMEM_DATA (default ~/.codemem), GIT_ROOT (default /srv/git), CODEMEM_PORT (default 8055).
set -euo pipefail
src=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$src"
DATA_DIR=${CODEMEM_DATA:-$HOME/.codemem}
PORT=${CODEMEM_PORT:-8055}
say() { printf '  %s\n' "$*"; }

if [ "${1:-}" != "--data" ]; then
    [ -x venv/bin/python ] || uv venv -q venv --python 3.13
    uv pip install -q --python venv/bin/python -e .
    say "venv ready"
    mkdir -p "$DATA_DIR"
    say "data dir $DATA_DIR"

    # User units: no root needed. Run `loginctl enable-linger $USER` once so they run without a login session.
    # The unit files assume the repo lives at ~/codemem; edit WorkingDirectory/ExecStart if it does not.
    UNIT_DIR="$HOME/.config/systemd/user"; mkdir -p "$UNIT_DIR"
    install -m 644 systemd/*.service systemd/*.timer "$UNIT_DIR/"
    systemctl --user daemon-reload
    systemctl --user enable --now codemem.service codemem-sync.timer codemem-backup.timer
    systemctl --user restart codemem.service
    say "systemd --user: codemem.service, codemem-sync.timer (6h), codemem-backup.timer (02:45)"
    sleep 2
fi

export CODEMEM_DB=${CODEMEM_DB:-$DATA_DIR/codemem.db}
venv/bin/python -m codemem.cli seed
venv/bin/python -m codemem.cli docs
venv/bin/python -m codemem.cli backfill | tail -1
venv/bin/python -m codemem.cli gitea
venv/bin/python -m codemem.cli scan | tail -1
venv/bin/python -m codemem.cli embed --all | tail -1
echo
curl -sf "http://127.0.0.1:$PORT/health" && echo
echo "Web UI: http://<this-host>:$PORT/   MCP: http://<this-host>:$PORT/mcp"
echo "Client on this machine: client/install-client.sh"

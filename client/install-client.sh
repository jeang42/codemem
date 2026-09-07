#!/usr/bin/env bash
# Install the codemem client on a Linux/macOS machine: MCP registration + Claude Code hooks.
# Idempotent. Needs python3 and the `claude` CLI on PATH.
set -euo pipefail
URL=${CODEMEM_URL:-http://localhost:8055}
DEST=${CODEMEM_CLIENT_DIR:-$HOME/.codemem}
src=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

mkdir -p "$DEST"
cp "$src/codemem_hook.py" "$src/codemem_agent.py" "$DEST/"
chmod +x "$DEST"/*.py
echo "client scripts -> $DEST"
# /codemem slash command (user scope): help + brief, or a search when given an argument
mkdir -p "$HOME/.claude/commands"
cp "$src/commands/codemem.md" "$HOME/.claude/commands/codemem.md"
echo "slash command -> ~/.claude/commands/codemem.md  (/codemem, /codemem <query>)"

# 1. MCP server, user scope (all projects on this machine)
CLAUDE=$(command -v claude || ls "$HOME/.claude/local/claude" 2>/dev/null || true)
if [ -n "$CLAUDE" ]; then
    "$CLAUDE" mcp remove -s user codemem >/dev/null 2>&1 || true
    "$CLAUDE" mcp add --transport http --scope user codemem "$URL/mcp"
    echo "registered MCP server codemem -> $URL/mcp"
else
    echo "WARNING: claude CLI not on PATH; run manually:  claude mcp add --transport http --scope user codemem $URL/mcp"
fi

# 2. Hooks in ~/.claude/settings.json (merged, other settings preserved)
python3 - "$DEST" "$URL" <<'PY'
import json, os, sys
dest, url = sys.argv[1], sys.argv[2]
path = os.path.expanduser("~/.claude/settings.json")
try:
    s = json.load(open(path))
except (OSError, ValueError):
    s = {}
hooks = s.setdefault("hooks", {})
cmd = f'CODEMEM_URL={url} python3 "{dest}/codemem_hook.py"'
for ev in ("SessionStart", "SessionEnd"):
    lst = hooks.setdefault(ev, [])
    lst[:] = [h for h in lst if "codemem_hook" not in json.dumps(h)]
    lst.append({"matcher": "", "hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump(s, open(path, "w"), indent=2)
print(f"hooks written to {path}")
PY

echo
echo "Test:  curl -s $URL/health"
echo "Scan this machine later with:  python3 $DEST/codemem_agent.py ~/some/dir"

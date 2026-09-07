#!/usr/bin/env python3
"""Cross-platform codemem client install (Windows, macOS, Linux). Stdlib only.

Copies the hook, agent and slash command into place, registers the MCP server at user scope,
and merges the SessionStart/SessionEnd hooks into ~/.claude/settings.json. Other settings are
preserved. Re-runnable. Uses the `claude` CLI for the MCP registration when it is on PATH,
otherwise writes the same entry into ~/.claude.json directly (what `claude mcp add --scope user` does).

    python client/install_client.py            # defaults: http://localhost:8055
    CODEMEM_URL=http://host:8055 python client/install_client.py
"""
import json, os, shutil, subprocess, sys
from pathlib import Path

URL = os.environ.get("CODEMEM_URL", "http://localhost:8055").rstrip("/")
HOME = Path.home()
DEST = Path(os.environ.get("CODEMEM_CLIENT_DIR", HOME / ".codemem"))
SRC = Path(__file__).resolve().parent
PY = "python" if os.name == "nt" else "python3"


def say(msg):
    print(f"  {msg}")


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy(path, str(path) + ".codemem-bak")
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    for f in ("codemem_hook.py", "codemem_agent.py"):
        shutil.copy(SRC / f, DEST / f)
    say(f"client scripts -> {DEST}")
    cmds = HOME / ".claude" / "commands"
    cmds.mkdir(parents=True, exist_ok=True)
    shutil.copy(SRC / "commands" / "codemem.md", cmds / "codemem.md")
    say(f"slash command -> {cmds / 'codemem.md'}  (/codemem, /codemem <query>)")

    # MCP server, user scope
    cli = shutil.which("claude") or next((str(p) for p in [HOME / ".claude" / "local" / "claude"] if p.exists()), None)
    if cli:
        subprocess.run([cli, "mcp", "remove", "-s", "user", "codemem"], capture_output=True)
        r = subprocess.run([cli, "mcp", "add", "--transport", "http", "--scope", "user", "codemem", f"{URL}/mcp"],
                           capture_output=True, text=True)
        say(f"MCP server registered via claude CLI -> {URL}/mcp" if r.returncode == 0 else f"claude mcp add failed: {r.stderr.strip()[:200]}")
    else:
        cfg_path = HOME / ".claude.json"
        cfg = load_json(cfg_path)
        cfg.setdefault("mcpServers", {})["codemem"] = {"type": "http", "url": f"{URL}/mcp"}
        save_json(cfg_path, cfg)
        say(f"MCP server written to {cfg_path} (no claude CLI on PATH) -> {URL}/mcp")

    # Hooks
    settings_path = HOME / ".claude" / "settings.json"
    s = load_json(settings_path)
    hooks = s.setdefault("hooks", {})
    hook_cmd = f'{PY} "{(DEST / "codemem_hook.py").as_posix()}"'
    for ev in ("SessionStart", "SessionEnd"):
        lst = [h for h in hooks.get(ev, []) if "codemem_hook" not in json.dumps(h)]
        lst.append({"matcher": "", "hooks": [{"type": "command", "command": hook_cmd, "timeout": 10}]})
        hooks[ev] = lst
    save_json(settings_path, s)
    say(f"hooks merged into {settings_path} (backup: {settings_path}.codemem-bak)")

    print()
    print(f"Test:  {PY} -c \"import urllib.request;print(urllib.request.urlopen('{URL}/health').read().decode())\"")
    print("Restart Claude Code. Scan this machine later with:  " + f"{PY} {DEST / 'codemem_agent.py'} <dir>")


if __name__ == "__main__":
    main()

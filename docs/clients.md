# Client machines

codemem needs nothing on a client except the `claude` CLI, Python 3, and network access to the
server (`CODEMEM_URL`, e.g. `http://memory.example.internal:8055`). Four pieces get installed:

1. **MCP registration** (user scope, so every project on the machine gets it):
   `claude mcp add --transport http --scope user codemem $CODEMEM_URL/mcp`
2. **Hooks** in `~/.claude/settings.json`: `SessionStart` prints the project brief into context;
   `SessionEnd` posts an automatic session record from the transcript. Both run
   `~/.codemem/codemem_hook.py`, stdlib only, and exit 0 no matter what.
3. **Slash command** `~/.claude/commands/codemem.md`: `/codemem` shows help and the current project's brief; `/codemem <query>` runs a search.
4. **Scan agent** `~/.codemem/codemem_agent.py`, run by hand (or a scheduled task) once a directory
   is registered with `add_scan_root`.

Both installers are thin wrappers around `client/install_client.py` (stdlib Python), which merges
the hooks into `settings.json` with a backup beside it and, when no `claude` CLI is on PATH (the
desktop app does not add one), writes the MCP entry into `~/.claude.json` directly.

## Linux / macOS

    git clone <this repo>
    CODEMEM_URL=http://<server>:8055 codemem/client/install-client.sh

## Windows (native Claude Code, PowerShell)

    git clone <this repo>
    $env:CODEMEM_URL = "http://<server>:8055"
    .\codemem\client\install-client.ps1

The Claude Code desktop app on Windows uses the same `~/.claude.json` and `~/.claude/settings.json`,
so the same installer covers it; restart the app afterwards. Claude Desktop (the chat app) is
different: it adds remote MCP servers under Settings > Connectors by URL, and expects HTTPS, so a
plain-HTTP LAN address may be refused; put a TLS proxy in front of the port if that is wanted.

WSL counts as a separate machine: run the Linux installer inside it. Set `CODEMEM_MACHINE` in the
hook command if the hostname is not distinctive.

## Scanning a machine's directories

Nothing is scanned until you say so. When a directory is tidy enough:

    # from any Claude Code session
    add_scan_root(path="C:/Users/me/src", machine="workstation", note="main dev tree")

    # then on that machine
    python ~/.codemem/codemem_agent.py            # scans registered roots, posts to the server
    python ~/.codemem/codemem_agent.py ~/other    # explicit roots, also registers them

The agent is exhaustive: it walks up to six levels (`--depth N` to change), records every project,
and inside each one runs the same asset discovery the server uses (scripts, modules, hooks,
Dockerfiles, systemd units, MCP servers, prompts, skills, with last-change dates and commit counts
from `git log` where the directory is a repo, file mtimes otherwise). The server names and
describes the assets, links code shared with any other project on any machine, and rescores trust.
For Python assets the agent also ships the first 250 lines of source, so the server's model review
(`codemem review --stage 2 --machine <name>`) can read code that exists only on that machine.
`--no-assets` records projects only. Third-party clones are marked vendor and their insides skipped.

The server machine scans its own roots (`CODEMEM_SCAN_ROOTS`, default `~/projects`) on the sync timer.

## Verifying

    curl -s $CODEMEM_URL/health
    claude mcp list
    # in a session: call codemem stats

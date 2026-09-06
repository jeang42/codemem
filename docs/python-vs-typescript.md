# Python vs TypeScript for this server

Decision: **Python.** Reasons, in order of weight:

| | Python | TypeScript |
|---|---|---|
| Fits the host | The other MCP servers on the same box are Python FastMCP with a systemd unit each. Same patterns, same debugging | Would be the only TS service on the box |
| SQLite + FTS5 | `sqlite3` is in the stdlib and FTS5 is compiled in | Needs `better-sqlite3` (native build) |
| Embeddings, ranking | stdlib + a little math; scipy/numpy available if wanted | Fine, but less at hand |
| Git and filesystem scanning | `subprocess`, `pathlib`, `os.walk` are comfortable | Works, more ceremony |
| Runtime already installed | Python 3.13 and `mcp` SDK present | Would need Node + build step |
| Claude Code hooks on Windows/Mac | The hook script is stdlib Python, runs anywhere Python exists | Node is also everywhere, so a wash |
| MCP SDK maturity | Official `mcp` package, FastMCP built in | Official TS SDK is the reference implementation, slightly ahead on new spec features |

Where TypeScript would win: if the server had to ship as a single `npx` package for others, or if the
transport were stdio on each client machine. Neither applies. This is one LAN service, HTTP, on a
box whose other services are Python.

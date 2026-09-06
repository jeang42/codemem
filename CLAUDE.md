# codemem — context for Claude

This is the MCP server that other Claude Code sessions use as memory. When working here:

- Run against a scratch DB: `CODEMEM_DB=/path/test.db CODEMEM_PORT=8056 venv/bin/python -m codemem.cli serve`.
  The live instance runs under `systemctl --user status codemem` with the DB at `$CODEMEM_DB`
  (default `~/.codemem/codemem.db`) on port 8055.
- Every write goes through `codemem/store.py` so `search_index` stays correct. Never INSERT into
  a content table without calling `index_item`.
- Tool arguments all have defaults (some MCP clients drop calls that omit a required parameter).
- `mcp` is pinned `<2` (FastMCP 1.x API).
- After changing code: `systemctl --user restart codemem`. After changing docs: `venv/bin/python -m codemem.cli docs`.
- Labels: audience is `unrestricted` (default) | `professional` | `employer`; origin is `own` | `vendor`;
  visibility is `private` (default) | `shared` | `public`;
  maturity vocabulary is in `db.MATURITY`. Definitions live in docs/USER_GUIDE.md. Do not invent new labels.

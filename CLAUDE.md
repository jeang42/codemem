# codemem — context for Claude

This is the **upstream, public** repository (Apache-2.0). It is the only place codemem code is
written. The archived private checkout at `~/Coding/mcp_servers/system-coding-memory` shares no
commits with this one and must not be edited.

- The database path is set by `CODEMEM_DB` in the deployment's systemd unit, never in the repo.
  Never hardcode it, and never name a real one here.
- Test against a scratch DB, never the live one:
  `CODEMEM_DB=/tmp/test.db CODEMEM_PORT=8056 venv/bin/python -m codemem.cli serve`.
- Every write goes through `codemem/store.py` so `search_index` stays correct. Never INSERT into
  a content table without calling `index_item`.
- Tool arguments all have defaults (Onyx drops calls that omit a required parameter).
- `mcp` is pinned `<2`. FastMCP 1.x API.
- After changing code: `systemctl --user restart codemem`. After changing docs:
  `venv/bin/python -m codemem.cli docs`.
- **Nothing site-specific goes in this repo.** No machine names, LAN addresses, absolute home
  paths or personal project names, in code, docs or commit messages. Every tunable belongs in
  `codemem/config.py` with an environment override and a neutral default.
- Publish with `git push` (origin is `codemem.git` on the git server). A public GitHub remote
  will be added at first release.
- Labels: audience is `unrestricted` (default) | `professional` | `employer`; origin is `own` |
  `vendor`; visibility is `private` (default) | `shared` | `public`; maturity vocabulary is in
  `db.MATURITY`. Definitions live in docs/USER_GUIDE.md. Do not invent new labels.

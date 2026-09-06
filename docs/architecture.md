# Architecture

One Python process. FastMCP serves the MCP protocol at `/mcp`; Starlette custom routes on the same
app serve the ingest endpoints, the JSON API and the web UI. SQLite holds everything.

```
Claude Code (any machine) ──MCP /mcp──┐
other MCP clients ─────────MCP /mcp──┤
SessionStart hook ────GET /brief ────┤        ┌─ SQLite $CODEMEM_DB
SessionEnd hook ──POST /session/end ─┼─ codemem ┤   tables + FTS5 search_index + embeddings
post-receive hook ─POST /ingest/push ┤        └─ Ollama nomic-embed-text (optional)
codemem_agent.py ──POST /ingest/scan ┤
browser ────────────GET / , /api/* ──┘
```

## Records

| table | key | notes |
|---|---|---|
| project | name | audience, status, tags, languages, remotes, commit stats. Created on first sight from any source |
| location | (machine, path) | working copies. branch, dirty, key files, README head |
| asset | (name, kind) | the anti-retooling table. `usage` is the one line needed to reuse it |
| note | id | kinds: session, session-auto, decision, howto, resource, issue, idea, note, handoff |
| commit | (project, hash) | message, files, who pushed from where |
| link | (from, to, relation) | between projects and assets |
| doc | source path | ingested markdown, hash-checked |
| scan_root | (machine, path) | what to scan, per machine |
| embedding | (kind, ref_id) | packed float32 vectors |
| search_index | FTS5 | one row per record of every kind; `audience` copied from the project so filters need no join |

## Search

`search.search()` runs BM25 over `search_index` (porter stemming, prefix match on each token) and,
if Ollama answers, a cosine search over embeddings; the two rankings are fused with reciprocal rank
fusion (k=60) and the top hits hydrated from their tables. Commits are indexed for BM25 only; they
are numerous and rarely need semantic matching. `embed_pending()` runs in a background thread after
every write and in the sync timer.

## HTTP routes

| route | method | who |
|---|---|---|
| `/mcp` | MCP | Claude Code and other MCP clients |
| `/health` | GET | monitoring |
| `/ingest/push` | POST `{repo, ref, old, new, who, from}` | post-receive hook |
| `/ingest/scan` | POST scan payload | codemem_agent.py on other machines |
| `/scan_roots?machine=` | GET | codemem_agent.py |
| `/brief?path=&machine=` | GET text | SessionStart hook |
| `/session/end` | POST | SessionEnd hook |
| `/api/search, /api/projects, /api/project/{name} (GET, PATCH), /api/assets, /api/notes, /api/activity, /api/docs, /api/stats` | JSON | web UI, scripts |
| `/` | GET | web UI |

No authentication: intended for a trusted network where the data is not secret to anyone who can
reach the port. Do not expose it beyond that without a proxy that authenticates.

## Maturity

`project.maturity` and `asset.maturity` (plus `_note`) use the vocabulary in `db.MATURITY`. The value is
copied into `search_index.maturity` (assets fall back to their project's rating) so both search legs can
filter without joins, and `db.MATURITY_RANK` multiplies the fused score: authoritative 1.6, usable 1.2,
unrated 1.0, antiquated 0.7, sunset 0.6, broken 0.5, junk 0.35. Adding a column to the FTS table required
a migration: `db._migrate` drops and rebuilds `search_index` while keeping embeddings.

## Discovery

`codemem/discover_core.py` holds the pure rules (candidate files, kind guessing, docstring/usage/symbol
extraction, git history, blob hashing) with no DB or server dependency. The server's `discover.py`
runs it over bare repos; the installer copies the same file next to the agent as `codemem_discover.py`
so remote machines produce identical asset records, posted inside the scan payload and stored by
`discover.ingest_assets()`. `link_shared_code()` works from the stored `blob_hash`, `symbols` and `func_hashes`
columns, so identical files, near-identical files and copied functions are linked across projects
and machines alike. `python_deep()` in the core extracts imports, per-function hashes (body
normalized by `_Normalize`: positional identifier renaming, docstrings/annotations/decorators
dropped, long string constants collapsed) and signatures. `imports` and `signatures` are part of the
asset's indexed body, so both BM25 and embeddings see them. `trust.imported_by_counts()` maps
file stems to importing projects for the reuse signal.

## Trust

`trust.compute_all()` writes `trust` and `trust_breakdown` (JSON) on every project and asset; runs from
`codemem trust` and the sync. Inputs: dates and counts already in the tables, `link`, active systemd
units from both scopes, and optionally the grade column of a review table (`CODEMEM_REVIEW_TABLE`, a
markdown table with a backticked project name column and an A to F grade column). Weighted mean over
the signals that have evidence, then maturity floor/cap, supersession and status caps. `verify()` sets
`verified_at` and recomputes. `search.search()` multiplies fused scores of projects and assets by
0.85 + 0.3 * trust/100.

## Audience

`project.audience` is free text, default `unrestricted` (`config.DEFAULT_AUDIENCE`). Other labels in
use: `professional`, `employer`. Every tool that reads accepts `exclude_audience="a,b"`. The web UI has
a "hide" box that does the same. The server never applies a filter on its own. The value is copied into
`search_index.audience` so both search legs filter without joins.

## Origin

`project.origin` is `own` or `vendor`. `scan.ingest_scan` sets `vendor` when
`store.is_vendor_remote()` says the remote's owner is not in `config.OWN_REMOTE_OWNERS` (when that
list is configured), or when the directory name looks like a package cache. Copied into
`search_index.origin`; `search` and `list_projects` exclude vendor unless asked. This is the one filter
that IS applied by default, because vendor clones are not the user's work and otherwise dominate results
by sheer size.

## Migrations

`db._migrate` runs at every connect: adds missing columns, applies one-shot data relabels (the
`personal` to `unrestricted` audience rename), and rebuilds `search_index` when its column set changed,
keeping embeddings. Add new columns there, never by hand.

# codemem

**Your coding agents keep rebuilding what you already have. codemem remembers what they built,
across every project and every machine, and scores each piece on evidence of whether it worked.**

It is an MCP server for Claude Code and other MCP clients. It knows what projects exist, where
they live, what reusable pieces they contain, what each session did and decided, every commit
pushed to the git host, and how to do recurring things. Everything is searchable from any
machine, from an agent, or from the web UI. It is written to *during* work, not after.

One Python process, SQLite, no external services required.

## What makes it different

Inventory scripts snapshot a directory and you read the report once. Registries list what people
published. codemem does neither.

- **Trust from evidence, not from stars.** Every project and asset gets a computed score from
  how recently it changed, how much work went into it, whether it is actually deployed and
  running, whether anything else imports it, and whether a human vouched for it. `verify` records
  "I ran it today and it works" and restarts the freshness clock. Scores are recomputed on
  every sync, so they decay when you stop touching something.
- **It finds the copies you forgot.** Discovery mines every repo at HEAD for scripts, modules,
  service units, Dockerfiles and MCP servers, hashes their functions, and links repos that share
  code. It will tell you that you wrote the same retry wrapper in three places.
- **Agents feed it automatically.** A session hook records what each Claude Code session did,
  and gives the next session a brief on the project it just opened. The catalogue builds itself.
- **Maturity is a first-class label.** `authoritative`, `usable`, `experimental`, `antiquated`,
  `sunset`, `broken`, `junk`, each with a reason. Search ranks by it, and you can exclude the
  ratings you should not build on.
- **Your code stays where it is.** codemem stores metadata: names, descriptions, imports,
  symbols, function hashes, commit records. It does not copy your source anywhere.

## Endpoints

| Endpoint | What |
|---|---|
| `http://<host>:8055/mcp` | MCP (streamable HTTP) for Claude Code and other MCP clients |
| `http://<host>:8055/` | Web UI: search, projects, assets, notes, activity, docs. Dark mode. |
| `http://<host>:8055/api/…` | JSON API used by the UI (see docs/architecture.md) |
| `http://<host>:8055/health` | liveness |

Normally runs on the git host, the machine holding the bare repos, so it can read them directly.

> **Security: codemem has no authentication.** It is built for a trusted network, and anyone who
> can reach the port can read and write everything. Do not expose it to the internet without an
> authenticating proxy in front of it. See docs/architecture.md.

## Requirements

Python 3.11 or newer, and git. Optional: a git host with bare repos for the commit feed, Gitea
for descriptions and web links, and [Ollama](https://ollama.com) for semantic search, drafted
descriptions and the review pass. Everything works without the optional pieces; search falls
back to BM25 alone.

## Why

A developer with many project folders across several machines has no way for a new Claude Code
session to know that a retry wrapper, a Gradio helper, a GPU monitor or a data pipeline already
exists somewhere. So they get rebuilt. Snapshot inventory scripts (run, read, forget) do not fix
that. codemem is the live, writable layer: it is written to during work, not after.

The content is whatever is there. codemem does not filter or judge. Each project carries an
`audience`: `unrestricted` (the default), `professional`, or `employer`, so a search or a view can
exclude one when the context calls for it. Nothing is hidden unless asked, with one exception:
cloned third-party repos are marked `origin = vendor` and stay out of search and lists until you
ask for them. See docs/USER_GUIDE.md.

## Quick start

Server (once, on the git host):

    ./install.sh                # venv, user-scope systemd units, seed, backfill, gitea, scan, embed

Client (each machine, once):

    CODEMEM_URL=http://<host>:8055 client/install-client.sh    # Linux/macOS: MCP server + SessionStart/SessionEnd hooks
    client\install-client.ps1                                   # Windows PowerShell (set $env:CODEMEM_URL first)

Then in any Claude Code session the `codemem` tools are available and every session starts with a
brief of the current project (if codemem knows it) and ends with an automatic session record.

## What Claude sees

| Tool | Use |
|---|---|
| `search` | hybrid BM25 + embedding search over everything. First call before building anything |
| `project_brief` | everything about one project, by name or working directory |
| `list_projects` | filter by status, tag, machine, audience, visibility |
| `update_project` | description, purpose, status, tags, audience, origin (own/vendor), visibility, maturity |
| `register_asset` | record a reusable script/module/prompt/skill/service with a one-line usage |
| `find_assets` | search or list assets by kind/tag/project, or by imported library |
| `log_session` | what was done, decided, used, abandoned, and what is next |
| `add_note` | decision, howto, resource, issue, idea |
| `link_items` | project uses / could-reuse / supersedes / derived-from another |
| `rate` | maturity: authoritative, usable, experimental, antiquated, sunset, broken, junk, with a why |
| `verify` | "I ran it today and it works": restarts the computed trust score's freshness clock |
| `handoff` / `list_handoffs` | track one implementation replacing another through candidate, shadow, verified, promoted |
| `howto` | how to publish, add a machine, use codemem, plus whatever docs are ingested |
| `activity` | commits and sessions across all machines, last N days |
| `add_scan_root` / `scan_path` | register a directory to scan; scan now (server machine) |
| `stats` | counts, machines, index coverage |
| `help` | workflow, tool list, label vocabularies. Also `/codemem` in Claude Code, `/codemem <query>` searches |

## Where the data comes from

- **Git host**: every bare repo in `GIT_ROOT` is backfilled; a post-receive hook posts each push to
  `/ingest/push` so new commits appear within seconds. See docs/git-commit-feed.md.
- **Gitea** (optional): descriptions, topics and web URLs, via its API with a read token.
- **Local scan**: project directories under registered roots, per machine. On the server this
  is automatic; other machines run `client/codemem_agent.py` once a root is registered.
- **Discovery**: every own repo is mined at HEAD for scripts, modules, units, Dockerfiles,
  prompts and MCP servers, recorded as `auto-discovered` assets, with shared-code links between repos.
- **Docs**: markdown from `CODEMEM_DOC_SOURCES` (this repo by default), re-hashed every sync.
- **Claude Code**: session records from the hook, plus whatever Claude writes with the tools.
- **Local model** (optional, via Ollama): embeddings for semantic search, drafted descriptions,
  and a targeted review of near-duplicate functions and thinly described code.

## Layout

    codemem/        the server package
      config.py     every tunable, env-overridable
      db.py         schema, connection, single FTS5 index
      store.py      all writes (keeps the index in step)
      search.py     BM25 + Ollama embeddings, reciprocal rank fusion
      gitsync.py    bare-repo backfill, push ingest, push.log, Gitea
      scan.py       project discovery + location records
      discover*.py  asset discovery, shared-code linking (core is shared with the remote agent)
      trust.py      computed trust scores
      review.py     targeted model review
      describe.py   model-drafted project descriptions
      knowledge.py  doc ingest, howto/asset seed
      server.py     MCP tools, HTTP routes, JSON API
      cli.py        serve | backfill | gitea | docs | seed | scan | embed | reindex | backup | sync | discover | review | trust | describe | purge
      web/          the single-file web UI (+ vendored marked.js)
    client/         hook, remote scan agent, slash command, per-OS installers
    systemd/        user-scope units: service, sync timer (6h), backup timer (02:45)
    docs/           user guide, architecture, git-commit-feed, clients, operations, python-vs-typescript

Storage: `$CODEMEM_DB` (default `~/.codemem/codemem.db`, SQLite, WAL). Backups: `$CODEMEM_BACKUP_DIR`
(default `~/.codemem/backups/`).

## Status and contributing

Extracted from a system that has been running daily against 165 projects, 735 assets and 2,000+
commits across three machines. It is stable for that use, but it has had one operator, so expect
rough edges the moment your layout differs from that one. Issues and pull requests welcome;
please open an issue before a large change so we can agree on the shape.

## License

Apache License 2.0. See LICENSE.

Copyright © 2026 Jeff Angelcyk.

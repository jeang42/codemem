# Operations

## Service

    systemctl --user status codemem            # the server
    systemctl --user restart codemem           # after code changes
    journalctl --user -u codemem -f            # logs
    systemctl --user list-timers 'codemem*'    # sync (every 6h) and backup (02:45)

User-scope units; run `loginctl enable-linger $USER` once so they run without a login. Unit files
live in `systemd/` and are installed by `install.sh`. They assume the checkout is at `~/codemem`.

## CLI

    venv/bin/python -m codemem.cli <cmd>
      serve       run the server (what the unit does)
      backfill    ingest every commit from every bare repo (idempotent)
      gitea       descriptions/topics/urls from Gitea
      docs        re-ingest markdown knowledge (hash-checked)
      seed        howto notes and known assets
      scan [dir]  scan local roots (or the given dirs) for projects
      embed --all embed everything that lacks a vector
      reindex     rebuild the FTS index from the tables (drops embeddings; run embed after)
      backup      gzip copy of the DB to CODEMEM_BACKUP_DIR (30 kept)
      sync        docs + seed + gitea + backfill + scan + discover + trust + embed: the timer job
      discover [repos] [--no-describe]  mine own repos for reusable assets + shares-code-with links (in sync)
      review [--stage 1|2] [--limit N] [--dry-run]  targeted model review (see USER_GUIDE)
      purge <name...>  remove a project and everything attached (see USER_GUIDE: Excluded)
      trust       recompute trust scores for all projects and assets (in sync)
      describe    draft descriptions for own projects lacking one (Ollama, CODEMEM_DESCRIBE_MODEL; tagged auto-described; --dry-run)
      stats

Set `CODEMEM_DB` when running the CLI against a database other than the default `~/.codemem/codemem.db`.

## Configuration

Everything is an environment variable with a default in `codemem/config.py`: `CODEMEM_DB`,
`CODEMEM_PORT`, `GIT_ROOT`, `CODEMEM_GIT_SSH_HOST`, `GITEA_URL`, `GITEA_TOKEN_FILE`, `OLLAMA_URL`,
`CODEMEM_EMBED_MODEL`, `CODEMEM_DESCRIBE_MODEL`, `CODEMEM_OWN_OWNERS`, `CODEMEM_EXCLUDE`,
`CODEMEM_DOC_SOURCES`, `CODEMEM_SCAN_ROOTS`, `CODEMEM_REVIEW_TABLE`, `CODEMEM_BACKUP_DIR`.
Put them in the systemd unit or a drop-in.

## Backup and restore

Nightly `codemem backup` copies the DB with SQLite's online backup API, gzips it to
`$CODEMEM_BACKUP_DIR/codemem-YYYYMMDD.db.gz`. Restore:

    systemctl --user stop codemem
    zcat ~/.codemem/backups/codemem-20260101.db.gz > ~/.codemem/codemem.db
    systemctl --user start codemem

Everything except notes and assets can be regenerated (`sync`); notes and assets are the part
worth the backup.

## Adding knowledge sources

`CODEMEM_DOC_SOURCES` (colon-separated globs). Add a path, run `codemem docs`. Files are re-hashed
on each sync so edits show up on their own. Point it at the docs of your git host, deployment
notes, and anything else `howto()` should be able to answer from.

## Embeddings

Ollama `nomic-embed-text` at `OLLAMA_URL`. If Ollama is down, search reports `mode: bm25` and
still works. `CODEMEM_EMBED=0` disables the embedding leg entirely.

## Health

    curl -s http://<host>:8055/health
    curl -s http://<host>:8055/api/stats | python3 -m json.tool

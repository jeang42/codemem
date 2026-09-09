# Contributing

Thanks for looking. This is a single-maintainer project extracted from a system that has run
daily against one person's machines, so the most valuable contributions right now are the ones
that tell me where it breaks when your setup differs from mine.

## Before a large change, open an issue

Small fixes: send the pull request. Anything that adds a tool, changes the schema, or alters how
records are written: open an issue first so we can agree on the shape. I would rather discuss it
early than turn down finished work.

## Running it locally

    git clone <this repo> && cd codemem
    python3 -m venv venv && venv/bin/pip install -e .
    CODEMEM_DB=/tmp/dev.db CODEMEM_PORT=8056 venv/bin/python -m codemem.cli serve

That gives you the MCP endpoint at `/mcp`, the web UI at `/`, and a throwaway database. Nothing
touches a real index. Python 3.11 or newer; everything else is optional.

Before pushing:

    scripts/smoke.sh venv/bin/python

It installs nothing, boots the server against a temporary database, and checks that health, the
web UI, the JSON API and the schema all come up. CI runs the same script on 3.11, 3.12 and 3.13.

## Two rules that are not negotiable

**1. Every write goes through `codemem/store.py`.** The FTS index is maintained alongside the
content tables, and a direct `INSERT` into a content table silently desynchronises search. If
you add a record type, add it to the store layer and call `index_item`. This rule is also what
makes future multi-tenancy tractable, so it will only get stricter.

**2. Nothing site-specific enters this repository.** No machine names, no LAN addresses, no
absolute home paths, no personal project names, in code, in docs, in defaults or in commit
messages. Every tunable belongs in `codemem/config.py` with an environment override and a
neutral default. CI enforces this with a grep and will fail the build.

## House style

- Every tool argument has a default. Some MCP clients drop calls that omit a required parameter.
- `mcp` is pinned below 2.x. This is the FastMCP 1.x API.
- Standard library where possible. A new runtime dependency needs a reason in the pull request.
- Match the surrounding code rather than introducing a new style. There is no formatter.
- Docstrings say what a module is for, in a sentence, at the top of the file.

## Labels are a fixed vocabulary

`audience` is `unrestricted`, `professional` or `employer`. `origin` is `own` or `vendor`.
`visibility` is `private`, `shared` or `public`. `maturity` is the seven-word vocabulary in
`db.MATURITY`. Definitions live in `docs/USER_GUIDE.md`. Do not invent new values; propose a
change to the vocabulary instead.

## Good first contributions

- A client installer or packaging for a platform I do not run.
- Language support in asset discovery. It parses Python well and other languages shallowly.
- Anything in `docs/` that was obvious to me and is not obvious to you.
- Bug reports with a scan root that produced a wrong or missing record.

## Licence

Contributions are accepted under Apache 2.0, the licence of this project. By opening a pull
request you confirm you have the right to contribute the code.

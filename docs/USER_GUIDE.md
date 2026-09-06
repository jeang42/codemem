# Using codemem day to day

codemem is a memory shared by every Claude Code session on every machine. It knows what projects
exist and where, what reusable pieces they contain, what each session did and decided, every commit
pushed to the git host, and how to do recurring things. Sessions read it at start (the brief),
query it while working (search, assets, howtos), and write to it when done (session log, assets,
ratings). The web UI at the server's root URL shows the same data without Claude.

This guide is about using it. How it works inside is in architecture.md; running it is in
operations.md; installing a client is in clients.md.

## In Claude Code

Every session starts with a brief if codemem knows the directory. Good habits to ask of Claude
(or put in a project's CLAUDE.md):

- "Search codemem before writing a helper." (`search`, `find_assets`)
- "When you finish something reusable, register it." (`register_asset` with a one-line `usage`)
- "Before you stop, log the session." (`log_session` with decisions and dead ends)
- "How do I publish this?" (`howto("publish")`)

The SessionEnd hook records the first prompt and final reply automatically, so even sessions where
nobody remembered to log still leave a trace. Those show as `session-auto` notes.

## Web UI

Overview, projects (editable description/status/tags/audience), assets, notes, activity, docs.
Search box searches everything. Theme toggle top right; follows the system theme by default.

## Audience: who a project is for

Every project has an `audience`. The labels and what they mean:

| audience | meaning |
|---|---|
| `unrestricted` | **the default.** Personal work, no content filtering applied |
| `professional` | portfolio-safe work you would show a client or put on a public GitHub |
| `employer` | code belonging to an employer; never mix into anything shared |

The labels are free text, so add others if needed, but keep them few: filters are by exact label.
codemem never applies a filter on its own. In a professional context ask for one:

    search("retry wrapper", exclude_audience="unrestricted")     # only professional/employer work
    list_projects(audience="professional")

or type `unrestricted` in the web UI's first "hide" box. When a project becomes portfolio-safe:

    update_project(name="my-cli-tool", audience="professional")

## Excluded: names that must never be recorded

Some names should never enter codemem at all, on any machine: set `CODEMEM_EXCLUDE` to a regex
(see `codemem/config.py` for the shape; match whole path segments or name tokens). Scans skip them,
the git feed and Gitea sync ignore repos by those names, and `codemem purge <project>` removes
anything that got in before the rule existed, with its locations, assets, notes, commits and links.
This is separate from `audience=employer`, which keeps a project but labels it; exclusion is for
things that should not be recorded at all. The default excludes nothing.

## Visibility: who may see it

Separate from audience (what kind of content) is **visibility** (who is allowed to see it):
`private` (the default: the owner only), `shared` (named people), `public` (anyone).
`update_project(name, visibility="shared")`, `list_projects(visibility="shared")`. A collaborator's
session should be given `visibility="shared"` or `"public"` filters; codemem itself does not enforce
access, the git host and the network do.

## Vendor code: other people's repos

The scanner records everything that looks like a project, including clones of third-party repos
(llama.cpp, an SDK unpacked from a tarball, and so on) that live alongside your own work. Those are
marked `origin = vendor` automatically when the git remote's owner is not one of yours
(`CODEMEM_OWN_OWNERS`, a comma list of your GitHub/GitLab handles; unset means no automatic
detection) or when the directory name looks like a package cache. Anything else, including repos
with no remote, is `own`. Mark one by hand when detection cannot tell:

    update_project(name="some-vendor-sdk", origin="vendor")

Vendor projects are **hidden from `search` and `list_projects` by default**, so they never crowd out
your own work or get mistaken for it. They are still recorded, still in the brief when you open
their directory, and available with `include_vendor=True` / `origin="all"` (the "vendor" checkbox in
the web UI) when you want to know which forks you have.

## Rating what is usable and what is not

Every project and asset can carry a **maturity** rating and a note saying why:

| rating | meaning |
|---|---|
| authoritative | the one to use; maintained and trusted |
| usable | works, reuse with normal care |
| experimental | unproven; may be worth building on |
| antiquated | works but superseded or dated; prefer something newer |
| sunset | being retired; do not build on it |
| broken | does not work as is |
| junk | not worth reusing; kept for reference only |

    rate(name="pipeline-v1", maturity="antiquated", note="superseded by pipeline-v2")
    rate(name="pipeline-v2", maturity="authoritative")
    rate(name="deploy.sh", asset_kind="script", maturity="authoritative")
    rate()                                   # vocabulary + everything rated so far

Assets inherit their project's rating unless rated themselves. Ratings do three things: they show as
a badge everywhere (brief, search results, web UI), they weight search so authoritative results float
up and junk sinks, and they can be excluded outright with `exclude_maturity="junk,broken,sunset"` on
`search`, `find_assets` and `list_projects`, or the second "hide" box in the web UI. Unrated things
rank normally, so nothing changes until you rate it.

## Trust: computed, with the reasons shown

Every project and asset carries a **trust** score, 0 to 100, recomputed on every sync from evidence
the repos already give. It is not a judgment, it is arithmetic, and the breakdown is always beside it
(hover the badge in the web UI, or read `trust_breakdown`).

Project signals and weights: freshness 30 (half-life one year from the last commit anywhere),
activity 15 (commit count, saturating around 100), hygiene 15 (README, CLAUDE.md, pinned deps,
Dockerfile or run script, installer; zero for names like `.bad`, `_orig`, `test`), deployed 15 (one of
its systemd units or MCP servers is running on this box), reuse 10 (incoming links, shared code),
review 15 (an A to F grade from an external code-review table, where one is configured with
`CODEMEM_REVIEW_TABLE`). Signals with no evidence drop out rather than count as zero. A `supersedes`
link pointing at it caps trust at 40; status abandoned or archived caps it in the 30s.

Asset signals: freshness 30 (last change to that file), churn 15 (how many commits touched it),
description 10 (real docstring beats a model draft beats "Unclear"), deployed 15, curated 10 (a person
registered it rather than the scanner), and the parent project's trust 25.

**Maturity wins over trust.** authoritative floors it at 85, usable at 60; antiquated caps at 45,
sunset 35, broken 25, junk 15. So rate what you know, and let trust cover the rest.

**Verifying restarts the clock.** When you have actually run something and it worked:

    verify(name="pipeline-v2", note="ran the full pipeline end to end")
    verify(name="deploy.sh", asset_kind="script")

That records `verified_at`, and freshness then decays from the verification (half-life 180 days)
instead of the last commit. The "verified today" button on a project page or asset card does the same.
Verify after exercising the code, not after reading it.

Trust nudges search ranking mildly (a factor between 0.85 and 1.15) so recent, deployed, reviewed
work edges ahead of stale duplicates; maturity's weight is stronger. Bands in the overview: high is 70
and up, medium 40 to 69, low below 40.

## Discovered assets

`codemem discover` (also part of the 6-hourly sync) mines every own repo at HEAD, straight from the
bare repos, for pieces worth reusing: scripts with a main or argparse, top-level and `tools/`
modules, shell scripts, hooks, Dockerfiles and compose files, systemd units, MCP servers, prompts
and skills. Each becomes an asset named `file (project)` tagged `auto-discovered`, with the module
docstring or header comment as description (or a model draft tagged `auto-described`, or "Unclear:"),
a usage line built from its argparse flags or exports, last change date, commit count, blob hash and
size. Tests, examples, vendored and generated code are skipped. Re-running updates dates and never
overwrites a description you wrote.

For Python files it goes one level deeper in the same parse: the **imports** (so the brief lists a
project's third-party dependencies and "which projects use chromadb" is a search away), a
**normalized hash of every function body** of six lines or more (local names, docstrings, annotations
and decorators stripped, so a renamed copy still matches), and the **signatures** with their first
docstring line, which go into the search index so "retry a request with backoff" finds the function
even when the filename says nothing.

It also finds retooling at three levels: files with identical content in two repos, Python files whose
function names overlap by half or more, and individual functions of eight lines or more whose
normalized bodies match across projects. Each produces a `shares-code-with` link between the projects
naming the files or functions. Those links appear in the brief and on the project page.

Reuse feeds trust: a module imported by other projects (by its file stem, ignoring generic names like
`config` or `utils`) gains, and so does the project that owns it.

Expect several hundred discovered assets on a machine with many repos. The web UI's Assets page
filters by tag, project and kind and sorts by last code change or commit count;
`find_assets(tag="auto-discovered")` does the same. Rating a discovered asset
(`rate(name, maturity, note, asset_kind=...)`) removes nothing, it just adds your judgment on top.

## Model review: targeted, not blanket

`codemem review` reads code with the local model (`CODEMEM_DESCRIBE_MODEL`) only where the parser
cannot answer:

1. **Near-duplicates.** Functions with the same name in two projects whose normalized hashes differ
   but sizes are close. The model rules same, variant or different and says what differs and which
   is better. Confirmed pairs are added to the projects' `shares-code-with` link, prefixed `model:`.
2. **Thin descriptions on trusted code.** Assets with trust 60 or more whose description is
   "Unclear", model-drafted, or very short. The model reads the file and records what it does, the
   risks it sees (hardcoded paths or hosts, credentials, swallowed exceptions, TODOs, destructive
   operations), how to reuse it, and its confidence. Placeholder descriptions are replaced; tags
   become `model-reviewed`.

Reviews show as a "model review" panel on the asset card (Assets page, project page, search results)
and the text is searchable. They are opinions from a model that read the file once; verify before
acting on a risk. Re-running skips anything already reviewed.

## Auto-drafted descriptions

`codemem describe` asks the local model to draft a description and purpose for own projects that have
none, from the README head, key files and recent commit subjects. Drafts are tagged `auto-described`
and say "Unclear:" when the evidence is thin. They are placeholders: when you write a real
description with `update_project`, remove the tag.

## Handoffs (one implementation replacing another)

When new code replaces an old implementation, record the process here:
`handoff(name, stage=..., old=..., new=..., promotion_class=..., report=..., note=...)`
appends a dated entry to one note per handoff; `list_handoffs()` (or `list_handoffs(stage="shadow")`)
shows what is in flight. Stages: candidate, shadow, verified, promoted, retired, rolled-back.
Promotion classes: library, service, agent-facing, pipeline, data-store. The Notes page filters by
kind `handoff`.

## Curating

The scanner and the git feed give you the skeleton. The value comes from:

- **purpose** on projects: one sentence on why it exists
- **assets**: the pieces you would want to find again
- **links**: `link_items("chat-app", "persona-lib", "uses", "same persona loader")`
- **decisions**: `add_note(kind="decision", title="Why SQLite not Postgres", body=...)`

## When the brief says "no record of <directory>"

The scanner only finds directories that look like projects (a `.git`, a `pyproject.toml`, a `CLAUDE.md`
and so on) under registered roots. A plain folder of scripts, or a directory that only contains other
repos, is invisible until attached by hand:

    update_project(name="my-tools", description="...", path="/home/me/tools")

`path` records the location (and, on the server, reads the directory's git state and README head), so
the next session started there gets a brief. Give `machine` too when attaching a path on another box.

## Other machines

See docs/clients.md. Register a directory with `add_scan_root` only when it is organised enough to
be worth indexing; until then the machine still gets briefs and logs sessions.

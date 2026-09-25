# Publishing: where codemem is released, and how

codemem is published in four public places. This page is the record of where they are, what
updates each one, and what is still manual.

## Published locations

| Where | Name | URL |
|---|---|---|
| GitHub (source, releases) | `jeang42/codemem` | https://github.com/jeang42/codemem |
| PyPI | `codemem-mcp` | https://pypi.org/project/codemem-mcp/ |
| MCP Registry | `io.github.jeang42/codemem` | https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.jeang42/codemem |
| Glama | `jeang42/codemem` | https://glama.ai/mcp/servers/jeang42/codemem |

The registry has no browsable page; the URL above is its JSON API. The latest entry is also at
`https://registry.modelcontextprotocol.io/v0/servers/io.github.jeang42%2Fcodemem/versions/latest`.

Names that are **not** ours: `codemem` on PyPI belongs to an unrelated project. The import
package and the command are `codemem` regardless of the distribution name.

Released versions: 0.1.0 (2026-09-25).

## What happens on a release

A release is one manual step: bump the version, then publish a GitHub release tagged
`v<version>` (see CONTRIBUTING.md, "Releasing"). Everything after that is automated or
automatic:

| Target | How it updates | Automated? |
|---|---|---|
| PyPI | `.github/workflows/publish.yml`, `pypi` job, Trusted Publishing (OIDC) | Yes, on release |
| MCP Registry | same workflow, `mcp-registry` job, `mcp-publisher login github-oidc` after PyPI serves the version | Yes, on release |
| Glama | Glama indexes the GitHub repository itself and builds the `Dockerfile` | Automatic, on Glama's schedule; nothing in this repo triggers it |
| GitHub release page | created by hand with `gh release create` | Manual, by design |

Neither PyPI nor the registry accepts the same version twice. A failed publish is fixed by
re-running the failed jobs (`gh run rerun <id> --failed`) if the fault was outside the package,
or by bumping the patch version and releasing again. Never delete and reuse a tag.

## Settings outside this repository

These are not in git and have to be kept in step by hand:

- **PyPI trusted publisher** for `codemem-mcp`: owner `jeang42`, repository `codemem`, workflow
  `publish.yml`, environment `pypi`. A repository or workflow rename breaks publishing until
  this is updated.
- **GitHub environment `pypi`**: deployment rules allow branch `main` and tags `v*`. Nothing
  else can deploy to PyPI.
- **Glama**: `glama.json` lists `jeang42` as maintainer. The listing is claimed on glama.ai while
  signed in with GitHub.

## Planned for the next release: rename to codemem-mcp

To stop the names disagreeing across sites, the next release renames everything public to
`codemem-mcp`. The command and the import package stay `codemem`. In order:

1. Rename the GitHub repository `jeang42/codemem` to `jeang42/codemem-mcp`
   (`gh repo rename codemem-mcp -R jeang42/codemem`). GitHub redirects the old URLs.
2. On PyPI, edit the trusted publisher for `codemem-mcp`: repository `codemem-mcp`. Publishing
   fails until this matches.
3. Update the repository in git: the `github` remote URL on every clone; the URLs in
   `pyproject.toml`; `server.json` (`name` to `io.github.jeang42/codemem-mcp`, `repository.url`,
   `repository.id` stays the same since GitHub keeps it across renames); the README `mcp-name`
   marker; this page and CLAUDE.md.
4. Bump the version (`codemem/__init__.py`, `server.json`), CHANGELOG entry, release
   `v0.1.1`. The workflow publishes PyPI and the new registry entry.
5. Deprecate the old registry entry:
   `mcp-publisher login github` then
   `mcp-publisher status --status deprecated --all-versions --message "Renamed to io.github.jeang42/codemem-mcp" io.github.jeang42/codemem`
6. On Glama, add or claim the listing under the new repository name and confirm the build
   passes inspection.
7. Update the table at the top of this page.

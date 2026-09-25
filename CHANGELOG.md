# Changelog

All notable changes to codemem. Versions follow [semantic versioning](https://semver.org/).

## 0.1.0 — unreleased

First release on PyPI, as `codemem-mcp` (the name `codemem` on PyPI belongs to an unrelated
project). The import package and the command are both still `codemem`.

- `pip install codemem-mcp` then `codemem serve`, or `uvx codemem-mcp serve` without installing.
  MCP over streamable HTTP at `http://localhost:8055/mcp`, web UI at `/`.
- Listed in the MCP Registry as `io.github.jeang42/codemem`.
- Projects, locations, reusable assets, session notes, decisions, commits and ingested docs in one
  SQLite database with a single FTS5 index; optional Ollama embeddings for hybrid search.
- Evidence-based trust scores, maturity labels, asset discovery with cross-repo function hashing.
- Claude Code SessionStart/SessionEnd hooks and client installers for Linux, macOS and Windows.

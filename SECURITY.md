# Security

## The most important thing to know

**codemem has no authentication.** Anyone who can reach its port can read and write everything
it knows. It is built for a single trusted machine or a trusted private network.

Do not expose it to the internet. If you need it reachable from outside, put an authenticating
reverse proxy in front of it and restrict the port to that proxy. This is a design limitation,
not a bug, and reports of "the API needs no credentials" will be closed as such.

## What codemem stores

Metadata about your code, not your code. Project and asset names, descriptions, tags, file
paths, import lists, symbol names, function hashes, commit records, and whatever you or your
agent write into notes and session records.

Two consequences worth thinking about before pointing it at a directory:

- **Paths and names are recorded.** If a directory name is itself sensitive, exclude it with
  `CODEMEM_EXCLUDE` before scanning, and purge anything already recorded with
  `codemem purge <project>`.
- **Session records contain whatever the agent wrote.** They are as sensitive as the work.

The database is a plain SQLite file with no encryption at rest. Protect it with filesystem
permissions and back it up somewhere you control.

## Reporting a vulnerability

Report privately, not in a public issue. Use GitHub's private vulnerability reporting on this
repository ("Security" tab, "Report a vulnerability"), which reaches the maintainer directly.

Please include what an attacker can do, the steps to reproduce, and the version or commit. A
proof of concept helps and is welcome. Expect a first response within a week. This is a
single-maintainer project, so please allow reasonable time for a fix before public disclosure,
and say up front if you plan to publish on a schedule.

In scope: anything that lets someone read or modify data they should not through the intended
interfaces, anything that executes code from ingested repository content, and path traversal in
scanning or ingestion.

Out of scope: the absent authentication described above, and anything that requires access to
the machine or the database file, since that access is already total.

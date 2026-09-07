---
description: codemem help and the brief for the current project
---
Call the codemem `help` tool and show its output verbatim in a code block. Then call `project_brief` with the current working directory ($PWD) and summarise what codemem knows about this project in a few lines: description, maturity, assets, recent notes. If it returns "no project matches", say so and offer to attach the directory with `update_project(name, description, path)`.

If the user gave an argument ($ARGUMENTS), treat it as a `search` query instead of showing help: run `search` with it and present the top results grouped by kind.

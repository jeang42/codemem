# The git commit feed

## What "feeding commits automatically" means

codemem expects to run on the git host: a machine with a directory of bare repos (`GIT_ROOT`,
default `/srv/git`) that developers push to over SSH. Every repo there shares one `post-receive`
hook. After a push lands, the hook sends one HTTP POST per updated ref to codemem:

    POST http://127.0.0.1:8055/ingest/push
    {"repo":"myproject","ref":"refs/heads/main","old":"<sha>","new":"<sha>","who":"alice","from":"10.0.0.12"}

A minimal hook body (append to your existing `post-receive`, or use as the whole thing):

    #!/usr/bin/env bash
    repo=$(basename "$PWD")
    who=${GIT_PUSHER:-${USER:-?}}; from=${SSH_CLIENT%% *}
    while read -r old new ref; do
      [ -n "${CODEMEM_URL:-http://127.0.0.1:8055}" ] && curl -sf -m 5 -X POST "${CODEMEM_URL:-http://127.0.0.1:8055}/ingest/push" \
        -H 'Content-Type: application/json' \
        -d "{\"repo\":\"$repo\",\"ref\":\"$ref\",\"old\":\"$old\",\"new\":\"$new\",\"who\":\"$who\",\"from\":\"$from\"}" \
        | sed "s/^/[$repo] codemem: /" || true
    done

codemem runs `git log old..new` in the bare repo, stores each commit (hash, author, date, full
message, files touched, who pushed, from which IP, when) and indexes it for search. The pusher
sees a line like `[myproject] codemem indexed 2 commit(s)` in their push output.

Consequences:

- A commit is in codemem within a second of being pushed, from any machine, with no client setup.
- Only pushed commits count. Local unpushed work is invisible until pushed (the location scan does
  record "dirty" and last local commit date, so you can see there is unpushed work).
- Commits carry their project, so `activity`, `project_brief` and `search` can answer "what did I
  touch last week" and "where did I write the retry wrapper" from commit messages and file lists.
- The hook never blocks a push: 5 s timeout, failures ignored, empty `CODEMEM_URL` disables it.

## Backfill

`codemem backfill` walks every bare repo with `git log --all` and inserts what is missing. It is
idempotent and runs inside the sync timer, so anything the hook missed (server down, repo created
by hand) is picked up within six hours. Historical "who pushed from where" is recovered from
`$GIT_ROOT/logs/push.log` if your hook writes one (tab-separated: timestamp, user, source IP, repo,
ref, action, count, old..new).

## Gitea

If a Gitea instance mirrors the bare repos, codemem reads its API (`/api/v1/user/repos`, read token
at `GITEA_TOKEN_FILE`, default `$GIT_ROOT/.gitea-token`) for descriptions, topics and the web URL of
each repo. Topics become project tags. Nothing is written to Gitea. Without a token the step is skipped.

## Repos not on the git host

Repos hosted elsewhere (GitHub, GitLab) appear through the location scan with their remote URL;
their commits are not fed. Mirroring them into `GIT_ROOT` brings them in through the same hook.

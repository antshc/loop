# PR babysitting

These user-owned workflows continuously discover new PRs and maintain them through
review and CI. They import only Loop's public API; nothing is added to the installed
`loop` package. Run from this checkout, or copy `babysit/`, `pr_scheduler/`, and
`pr_maintenance/` together into your harness's workflow package.

The native Python watcher uses [novotnyllc/babysit-pr](https://github.com/novotnyllc/babysit-pr)
as a behavioral reference. It does not install, pin, vendor, or execute that plugin.
Review handling follows the [Brain address skill](https://github.com/antshc/brain/tree/main/plugins/ralph/skills/address):
group feedback by underlying issue, investigate against the code, and answer each
pending thread. No upstream implementation is copied.

## Configure and run

Install Loop and authenticate `gh` and Copilot CLI. Clone every target repository;
its `origin` must match the configured GitHub name. Configure the normal Git author
identity in each checkout. Fork-head PRs currently block instead of pushing through
a different repository's remote.

Copy `workflows/pr_scheduler/settings.example.json` to a local configuration file.
Set repository paths and build/test argument arrays. Paths resolve relative to that
file. Both build and test commands are required; subprocesses run without a shell.
For shell-specific commands use an explicit shell argument array in trusted config.
The same build/test commands run before review edits and after each code fix.
If baseline verification fails alongside CI, Maintainer repairs CI before Address
can edit. Checks must work in a fresh worktree, including dependency setup if needed.

```sh
python -m workflows.pr_scheduler --config babysit.local.json
```

Default behavior:

- Author is `gh api user`'s login; set `author` to override discovery.
- A complete first scan baselines existing open PRs without enrolling them.
- Future open PRs by that author, including drafts, start independent babysitters.
- `--include-existing` enrolls existing PRs during the first baseline only.
- Scans and worker polls occur every 60 seconds; `interval` changes both.
- `concurrency` bounds active PR workers; one worker owns each PR through an OS lock.
- Different PRs run concurrently. Agents within one PR run sequentially.
- Merged/closed PRs finish. Enrolled PRs start again when reopened.
- Green CI and mergeability are milestones; an open PR stays under observation.
- No worker merges a PR or resolves review threads.

Run one PR directly, including an explicit retry of a blocked run:

```sh
python -m workflows.babysit --config babysit.local.json --pr https://github.com/owner/repo/pull/123
```

Or retry one enrolled PR while starting the scheduler:

```sh
python -m workflows.pr_scheduler --config babysit.local.json --retry https://github.com/owner/repo/pull/123
```

For OTLP, install `loop[otel]` and set `otel_endpoint`, e.g. `http://localhost:4318`.
Without that setting, no exporter is created. `copilot_review` defaults to false;
when true, Python makes a best-effort Copilot request once per SHA. A failed request
is recorded as unavailable and does not prevent maintenance. Pending Copilot review
prevents the ready milestone.

## Agent and Python ownership

| Actor | Owns |
|---|---|
| Address | Group review feedback, investigate validity with code citations, edit justified fixes, draft replies |
| Maintainer | Diagnose CI failures read-only; repair branch defects; identify transient infrastructure failures |
| Python | Discussion gate, worktrees, verification, commits, pushes, retries, replies, JSON checkpoints |

Profiles live in `agents/agents.py`. Their complete instructions live in
`agents/address.md` and `agents/maintainer.md`. Set models and reasoning effort in
the Python profiles if needed. Agent requests use fresh sessions. Agents return
validated JSON; they must not commit, push, amend, or use GitHub clients. Native
Copilot deny rules reinforce the prompts, and Python checks that HEAD did not
change unexpectedly. Host-user agent execution is not a sandbox.

Address groups every pending feedback item exactly once. Python runs the agent
for each issue, verifies build/tests, and commits that issue. It pushes the batch
once, confirms the remote SHA, and posts the drafted replies. Rejected pushes
produce no replies. Maintainer follows the same Python-owned verify/commit/push
sequence. Transient retries reserve a cycle before rerunning failed jobs, with
three cycles per SHA by default. A new SHA has a separate budget.

## Comment handling and recovery

Resolved threads and threads whose latest submitted comment is the operator's
answer are skipped. New reviewer follow-ups reopen work, with the previous answer
provided as context. An opening self-note or a latest `fix!:` note remains pending.
A new SHA alone does not reopen handled feedback. Review submissions and PR-level
comments have separate persistent handled keys. Unsupported authors are ignored;
repository collaborators, the operator, and supported review bots are accepted.
The workflow polls quietly without invoking agents when no work exists.

State is JSON only:

```text
.loop/pr-babysit/
    scheduler.json
    scheduler.lock
    prs/owner--repo--123.json
    prs/owner--repo--123.lock
    worktrees/owner--repo/123/
```

Keep `state_dir` outside tracked files. This repository ignores `.loop/`.
Atomic replacement and file fsync prevent partial JSON writes. Invalid JSON or
schema errors stop processing rather than silently resetting the baseline.
Changing discovery author/repositories requires a new state directory or explicit
migration. The OS releases locks when their processes die; lock files remain inert.

Observed feedback remains pending until publication and replies succeed. Batches
checkpoint their base/head commits, issue progress, and reply outbox. A failed push
can resume without another agent call. A reply posted just before a crash is
reconciled against GitHub before reposting. If an issue was interrupted after
verification and its commit completed, the journal checks its parent and subject
before adopting it. Unverified edits or unexpected commits block and remain in the
recovery worktree for inspection; they are never automatically committed or erased.

Inspect a retained worktree and its per-PR JSON before explicit retry. For failed
verification, repair or remove only the failed issue's uncommitted changes, retain
already verified commits, then retry. An interrupted verification/commit checkpoint
that did not create its commit needs manual reconciliation of the checkpoint and
worktree. Do not delete state files while a worker is active.

The scheduler resumes interrupted workers. Blocked workers resume when their review,
head, or CI inputs change, or on explicit retry. A worker that crashes without a
checkpointed outcome requires explicit retry. One PR's failure does not stop other
active workers. Shutdown stops owned workers; their unfinished work is preserved.

## Telemetry boundary

The scheduler creates no WorkflowBuilder, observer, spans, or metrics. Discovery,
registry access, dispatch, and scheduler sleeps produce only local logs. Workers
are separate processes without inherited distributed trace headers.

Each worker execution starts one `babysit` workflow root with repository, PR number,
execution ID, and SHA. It shares its observer with Address and Maintainer. Only
meaningful maintenance phases and readiness transitions are exported; routine
polling and waiting create no phase spans. Blocking/closure ends the root; a resumed
execution creates another independent root.

## Validate

```sh
python -m pytest workflows/babysit/tests workflows/pr_scheduler/tests
python -m pytest
```

Tests use fake GitHub responses and agents, local Git remotes, JSON failure
injection, and an in-memory span exporter. They never start live agents, request
reviews, post comments, or push to GitHub.

# PR babysitting

This user-owned Workflow discovers new PRs and maintains them through review and CI.
It uses only Loop's public API and is not included in the installed `loop` package.
Run from this checkout or copy `pr_babysit/` into your harness's Workflow package.

Native polling uses [novotnyllc/babysit-pr](https://github.com/novotnyllc/babysit-pr)
as a behavioral reference, without installing, pinning, vendoring, or wrapping it.
Review handling follows the [Brain address skill](https://github.com/antshc/brain/tree/main/plugins/ralph/skills/address).

## Structure

```text
pr_babysit/
    main.ts                      # CLI (commander) and entry point
    composition.ts               # InversifyJS composition root; tokens.ts holds the tokens
    discovery.ts                 # Discovery service
    worker.ts                    # Single-PR worker service
    file-lock.ts                 # OS file locks through Bun FFI
    settings.ts
    settings.example.json
    workflow/
        run.ts                   # Single-PR orchestration
        definition.ts            # WorkflowBuilder phases
        watcher.ts               # Discussion gate and CI decisions
        address.ts               # Review issue handling and response validation
        maintainer.ts            # CI diagnosis and repair
        maintenance.ts           # Maintenance pass wiring
        workspace.ts             # Named-worktree serialization and Loop composition
        changes.ts               # Verification and publication transaction
        records.ts               # Batch and outcome records
    infrastructure/
        scheduler.ts             # Discovery timing and worker processes
        signals.ts               # Graceful stop on SIGINT and SIGTERM
        github.ts                # GitHub queries and mutations
        git.ts                   # Git inspection, explicit commit/push, revision fetch
        verification.ts          # Build/test subprocesses
        state.ts                 # JSON checkpoints, registry, process locks
    agents/
        profiles.ts
        address.md
        maintainer.md
    tests/
        workflow/
        infrastructure/
        fixtures/
```

## Configure and run

Install Loop with `bun install` and authenticate `gh` and Copilot CLI. Configure each checkout's normal
Git author identity. Each origin must match the configured repository. Fork-head PRs
block instead of pushing through an unrelated remote.

Copy `settings.example.json` to a local configuration file. Paths resolve relative
to that file. Each repository requires build and test argument arrays, executed
without a shell. Include dependency preparation in those commands when needed in
a fresh worktree. Set `author` to override the authenticated GitHub user.

Run from the harness root, directly or through your own alias:

```sh
bun workflows/pr_babysit/main.ts --config babysit.local.json
bun workflows/pr_babysit/main.ts --config babysit.local.json --include-existing
bun workflows/pr_babysit/main.ts --config babysit.local.json --pr https://github.com/owner/repo/pull/123
bun workflows/pr_babysit/main.ts --config babysit.local.json --retry https://github.com/owner/repo/pull/123
```

A lock held by another process stops the run as Blocked with exit code 1. The first
Ctrl-C or SIGTERM stops discovery and gives each worker 10 s to finish; a second
signal force-kills the remaining worker process groups and exits immediately. A
worker interrupted on its own records `interrupted` and exits 130 (SIGINT) or 143
(SIGTERM).

The default first scan baselines existing PRs. `--include-existing` enrolls them
on that first scan. Future open PRs by the selected author, including drafts, start
independent workers. The default scan/poll interval is 60 seconds; `concurrency`
limits active workers. Enrolled PRs restart when reopened. Merged/closed PRs finish;
green CI is a milestone, so open PRs continue watching for late feedback.

`--pr` directly runs or retries one PR. `--retry` resumes one enrolled blocked PR
while starting discovery. Blocked executions otherwise resume only on changed head,
review, or CI inputs. A crashed worker without a recorded outcome requires explicit retry.

For telemetry, install the optional OpenTelemetry packages (`bun add @opentelemetry/api @opentelemetry/exporter-metrics-otlp-http @opentelemetry/exporter-trace-otlp-http @opentelemetry/resources @opentelemetry/sdk-metrics @opentelemetry/sdk-trace-base`) and configure `otel_endpoint`, for example
`http://localhost:4318`. Without it no exporter is created. `copilot_review` defaults
to false; enabling it requests a best-effort Copilot review once per SHA. Pending
review prevents the readiness milestone, while unavailable Copilot does not block work.

## Responsibilities

| Actor | Responsibility |
|---|---|
| Loop library | Create/reuse named worktrees; remove clean directories; preserve dirty work; retain branch commits; prune stale metadata |
| Address | Group review feedback, investigate with evidence, edit justified fixes, draft replies |
| Maintainer | Diagnose CI read-only, fix branch defects, identify transient failures |
| Workflow code | Verify, explicitly commit/push, post replies, retry CI, persist checkpoints, serialize named-worktree use |

Profiles and complete Markdown instructions stay together under `agents/`.
Agents must not commit, push, change branches, or use GitHub clients. Native deny
rules reinforce the prompts, and the Workflow checks actual HEAD changes. Host agent
execution is not a sandbox.

The Workflow commits each verified review issue and pushes once per batch, confirms the
remote SHA, then replies. Rejected pushes produce no replies. Threads remain open
for reviewers; no code merges PRs. Maintainer uses the same explicit publication
sequence. Three transient retry cycles per SHA are allowed by default; repeated
stale CI snapshots do not spend another cycle. Baseline verification must pass
before review edits; Maintainer can repair a failing CI baseline first.

## Worktree lifecycle and recovery

Every PR uses a persistent local branch `babysit/<owner>/<repo>/<number>` through
Loop's built-in `BranchStrategy`. There is no workflow-specific worktree strategy.
Worktree roots are explicitly configured absolute paths under the state directory.

| Case | Result |
|---|---|
| Run fails with a clean worktree | Loop removes the directory; local branch and commits remain |
| Run fails with uncommitted edits | Loop preserves the directory and the original failure |
| Run returns successfully with edits | Loop raises `WorktreeError`; no automatic commit |
| Temporary-branch merge succeeds | Loop merges into host and deletes the temporary branch |
| Temporary-branch merge fails/diverges | Loop retains the branch; Workflow resolves recovery |
| Stale worktree metadata | Loop prunes stale entries before creation |
| Orphan directory | Loop removes empty/link-only disposable orphans; preserves unknown user files |
| Two users of one named worktree | Workflow holds a repository/branch lock |
| Sandbox sync conflict | Not applicable: Loop currently has no isolated synchronization backend |

JSON records branch identity, base/head commits, issue progress, and reply outbox.
A clean publication failure can remove the directory safely: the next run recreates
it from the retained branch and publishes the existing commit without another agent
call. A dirty failure reuses the preserved directory only after validation. Unverified
edits and unexpected commits are never silently reset, committed, or discarded.

Inspect retained work before explicit retry. Repair or remove only the failed issue's
uncommitted changes, preserving prior verified commits. Interrupted commit journals
are reconciled using the actual parent and subject; incomplete verification/commit
checkpoints without a recognized commit require manual reconciliation.

Observed feedback is separate from handled feedback. Resolved/answered threads are
skipped; reviewer follow-ups and unanswered self-notes or `fix!:` comments become
pending. A new SHA alone does not reopen handled comments. Replies posted just before
a crash are reconciled against GitHub, and stale drafted replies do not answer newer
reviewer follow-ups. Repository collaborators, the operator, and known review bots
are accepted; unrelated authors are ignored.

State remains JSON only:

```text
.loop/pr-babysit/
    scheduler.json
    scheduler.lock
    prs/owner--repo--123.json
    prs/owner--repo--123.lock
    locks/<repository-and-branch-hash>.lock
    worktrees/babysit/owner/repo/123/
```

Keep state outside tracked files. Writes use file fsync and atomic replacement;
invalid state is reported rather than reset. OS locks release on process exit.
Changing discovery author/repositories requires a new state directory or migration.
One PR's blocker does not stop others. Shutdown stops owned worker processes;
unfinished work and local recovery branches remain available.

## Telemetry and validation

Discovery, dispatch, JSON access, and scheduler waits produce local logs only. The
scheduler creates no WorkflowBuilder or observer. Separate worker processes discard
inherited distributed trace headers. Each execution creates an independent `babysit`
root, shared with agent observations; routine polls/sleeps create no phase spans.
Retained worktrees end their active telemetry span without claiming disk removal.

```sh
bun test workflows/pr_babysit/tests
bun run test
```

Tests use fake GitHub/agent responses, isolated local Git remotes, JSON failures,
and in-memory spans. They never invoke live agents or contact GitHub. Signal handling,
process-group kill, and FFI locking are verified on Linux; macOS and Windows behavior is untested,
and Bun marks FFI experimental.

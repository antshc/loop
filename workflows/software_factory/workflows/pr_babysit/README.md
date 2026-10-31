# PR babysitting

This user-owned Workflow discovers new PRs and maintains them through review and CI.
It uses only Loop's public API and is not included in the installed `loop` package.
Run from this checkout.

Native polling uses [novotnyllc/babysit-pr](https://github.com/novotnyllc/babysit-pr)
as a behavioral reference, without installing, pinning, vendoring, or wrapping it.
Review handling follows the [Brain address skill](https://github.com/antshc/brain/tree/main/plugins/ralph/skills/address).

## Structure

`pr_babysit` is one declarative definition over the factory's Stage library, run by
Loop's Workflow runtime once per pull request. Git, GitHub and process helpers come from Loop (`loop.shared`); state,
pacing and telemetry infrastructure from [`workflows/software_factory/infrastructure`](../../infrastructure); the Stages are in
[`workflows/software_factory/stages`](../../stages) and the address-feedback activity is the
shared fragment in [`workflows/software_factory/flows`](../../flows).

```text
pr_babysit/
    definition.py                # the definition: validate origin, then poll until the pull request closes
    policy.py                    # trust rules, fingerprint, commit subjects, what a blocked run records
    settings.py, settings.example.json
    intake.py                    # discovery scheduler, one thread per PR, reads the Execution ledger
    legacy.py                    # one-time adoption of the earlier per-PR JSON state into the ledger
    __main__.py                  # CLI, services built by hand, console and OpenTelemetry spans
```

On every poll the definition does exactly one of: repair a confirmed merge conflict,
address trusted unhandled feedback
(clean tree, baseline check, group, resolve each issue, push once, reply once), maintain
a failing CI (diagnose; rerun a transient failure up to the retry budget per head, or fix
a broken branch and publish; then pace), request a Copilot review once per head, or
report readiness when it changed (then pace). A red baseline while CI is red maintains CI
instead of addressing feedback in that poll. A closed pull request completes the run;
anything stuck or unsupported blocks it. Conflict repair takes priority over feedback
and CI. Unknown mergeability waits for GitHub's next computation.

## Configure and run

Install Loop (`pip install -e ".[dev]"` from this checkout) and authenticate `gh` and Copilot CLI. Configure each checkout's normal
Git author identity. Each origin must match the configured repository. Fork-head PRs
block instead of pushing through an unrelated remote.

Copy `settings.example.json` to a local configuration file. Paths resolve relative
to that file. Each repository requires build and test argument arrays, executed
without a shell. Include dependency preparation in those commands when needed in
a fresh worktree. Set `author` to override the authenticated GitHub user.

```sh
python3 -m workflows.software_factory.workflows.pr_babysit --config babysit.local.json
python3 -m workflows.software_factory.workflows.pr_babysit --config babysit.local.json --include-existing
python3 -m workflows.software_factory.workflows.pr_babysit --config babysit.local.json --pr https://github.com/owner/repo/pull/123
python3 -m workflows.software_factory.workflows.pr_babysit --config babysit.local.json --retry https://github.com/owner/repo/pull/123
```

The default first scan baselines existing PRs. `--include-existing` enrolls them
on that first scan. Future open PRs by the selected author, including drafts, start
independent PR threads in the scheduler process. The default scan/poll interval
is 60 seconds; `concurrency` limits active workers. Enrolled PRs restart when reopened. Merged/closed PRs finish;
green CI is a milestone, so open PRs continue watching for late feedback.

`--pr` runs one PR directly. A run that ends blocked is *handed off* with the pull
request's fingerprint recorded on the run, and while it stays blocked no agent runs and
nothing is pushed for it. Discovery starts a worker for a blocked PR again only when its
fingerprint changed (head, base revision, review, or CI inputs), which first releases the blocked work so
the next run starts a fresh scope; `--retry <pr-url>` writes the release itself, whatever the
fingerprint. A worker that died without closing its run requires an explicit `--retry`.
Pressing Ctrl+C (or SIGTERM) stops a run after its current poll and exits 130.

For telemetry, install `loop[otel]` and configure `otel_endpoint`, for example
`http://localhost:4318`. Without it no exporter is created. `copilot_review` defaults
to false; enabling it requests a best-effort Copilot review once per SHA. Pending
review prevents the readiness milestone, while unavailable Copilot does not block work.

## Responsibilities

| Actor | Responsibility |
|---|---|
| Loop library | Create/reuse named worktrees; remove clean directories; preserve dirty work; retain branch commits; prune stale metadata |
| Address | Group review feedback, investigate with evidence, edit justified fixes, draft replies |
| Maintainer | Diagnose CI read-only, fix branch defects, identify transient failures, resolve merge conflicts |
| Factory runtime | Verify, commit each fix with the Workflow's subject, push, post replies, retry CI, record every attempt in the Execution ledger, serialize one pull request with a lock |

The role agents' profiles and complete Markdown instructions live with the typed role agents under [`workflows/software_factory/agents`](../../agents).
Agents must not commit, push, change branches, or use GitHub clients. Native deny
rules reinforce the prompts, and Python checks actual HEAD changes. Host agent
execution is not a sandbox.

Python commits each verified review issue and pushes once per batch, confirms the
remote SHA, then replies. Rejected pushes produce no replies. Threads remain open
for reviewers; no code merges PRs. Maintainer uses the same explicit publication
sequence. Three GitHub Actions transient retry cycles per SHA are allowed by default; repeated
stale CI snapshots do not spend another cycle. Baseline verification must pass
before review edits; Maintainer can repair a failing CI baseline first.

### Optional Jenkins recovery

The global `jenkins` object in [settings.example.json](settings.example.json)
applies to every watched repository. Missing configuration or `enabled: false`
performs no Jenkins requests or credential reads and preserves Actions behavior.
A disabled integration does not make a failed external check green.

To enable it, configure the HTTPS server origin, trusted classic job-root paths
(including trailing `/`), and a local `credentials_file`. Paths resolve relative
to the configuration file. Credentials use `jenkinsUser` and `jenkinsApiToken`;
keep that file outside Git and restrict its permissions. Do not put tokens in
status URLs or settings. Only recognized current-head failed statuses/checks on
the configured server are selected. Classic and Blue Ocean links must resolve
under a trusted job root to this PR's `PR-<number>` job.

Certificate verification is on by default. Supply `ca_bundle` for an internal CA.
An explicit `insecure_tls: true` disables verification for this integration and
logs a warning; do not combine it with `ca_bundle`. There is no global TLS bypass.
`timeout` bounds each request; `log_bytes` bounds the console tail (1 KiB to 1 MiB,
default 256 KiB). Credentials never follow redirects.

Jenkins history must identify the watched repository and provide an unambiguous
PR SCM revision `pullHash`. Multiple checkout SHAs are not substitutes for that
revision. Missing/incomplete retained history or ambiguous identities hand off
rather than guessing capacity. Jobs without supported PR revision metadata need
manual handling. Once observed, job identities survive head changes. Missing GitHub
statuses do not make a tracked job green: readiness waits for its new-head build,
and a failed tracked build still enters diagnosis. An entirely undiscovered job
with no check/status or retained identity cannot be inferred.

| Diagnosis | Recovery |
|---|---|
| Timeout, likely flaky test, Docker/container infrastructure failure | Restart the whole PR job |
| Other failure the maintainer judges likely transient | Restart with an evidence-backed rationale |
| Genuine test failure or branch defect | Investigate, fix, verify, commit, publish |
| Unknown cause, manual abort, inaccessible evidence | Hand off with a reason |

Diagnosis is read-only and treats console text as untrusted evidence. It
distinguishes infrastructure failure from a real test failure inside Docker.
An `ABORTED` result is inspected for a timeout versus an operator cancellation;
the result alone does not authorize restart.
The agent supplies a category and rationale; Python owns every Jenkins trigger
and code publication. API/auth/transport errors are not transient build failures.

The limit is **three total Jenkins builds per repository/PR/head**, including the
initial build and all observed manual/SCM builds: normally one original build
and at most two restarts. This is separate from the Actions `retries` setting.
Repeated observations do not spend capacity. After the third failure, the
Workflow hands off without a fourth build or another automatic fix for that
head. A verified, published fix creates a new head with a fresh budget; its normal
SCM-triggered build is awaited, not duplicated. Returning to an old SHA retains
its old accounting.

Restarts reuse supported string/choice/boolean parameters from the failed build,
validated against current job definitions, or current defaults if the original
build had no value. Unsupported or sensitive parameter types require manual
handling. The branch-based trigger cannot pin an immutable SHA: Python rechecks
the current head before submission and checks the actual build revision later.
A changed/ambiguous checkout hands off instead of being attributed to the old head.

Capacity and unresolved submissions are durably stored under `state_dir/jenkins`
with a per-PR lock. A slot is reserved and fsynced before one POST; `201` means
queued, not recovered. Queue identity is reconciled against build history.
An interrupted/lost response retains its reservation and requires investigation
when it cannot be reconciled; there is no blind resend. A queued/running job
waits. `--retry`, process restart and disable/re-enable do not erase this state.
Keep this directory alongside the Execution ledger; workers must share it for
cooperative locking. Independent human/SCM triggers cannot be globally capped
by the Workflow.

### Merge conflict recovery

Python pins the PR head, recovery head and actual base branch revision in the ledger,
then starts `git merge --no-commit --no-ff <base-sha>` in the clean recovery worktree.
Maintainer resolves conflicting files only; ambiguous intent blocks with the decision
needed. A clean merge needs no agent. Python stages resolutions, rejects remaining
conflict markers, runs the configured build/tests, and records the verified tree.
Only that tree can become the two-parent merge commit. Before publication, Python
rechecks the PR's head, base and open state, pushes without force, and confirms the
remote SHA. The next poll reads fresh CI and reviews. No rebase, force-push or PR merge
is performed.

Failed checks and interrupted edits retain the worktree for inspection. Interrupted
commit/publication attempts reconcile against the recorded tree and ordered parents;
an already published merge is not duplicated. A rejected push keeps the local commit:
after inspection and `--retry`, it is verified again and published without another
resolver call. Dirty or ambiguous recovery work is never discarded automatically.

New blocked fingerprints include the base ref/SHA. Older blocked records retain their
previous comparison until another head/review/CI input changes or `--retry` is used;
a base-only change cannot release a record that never observed its earlier base.

## Worktree lifecycle and recovery

Every PR uses a persistent local branch `babysit/<owner>/<repo>/<number>` through
Loop's built-in `BranchStrategy`. There is no workflow-specific worktree strategy.
Worktree roots are explicitly configured absolute paths: `worktrees_dir` in the settings file
(resolved relative to it), by default `worktrees` under the state directory.

| Case | Result |
|---|---|
| Run fails with a clean worktree | Loop removes the directory; local branch and commits remain |
| Run fails with uncommitted edits | Loop preserves the directory and the original failure |
| Run returns successfully with edits | Loop raises `WorktreeError`; no automatic commit |
| Temporary-branch merge succeeds | Loop merges into host and deletes the temporary branch |
| Temporary-branch merge fails/diverges | Loop retains the branch; Workflow resolves recovery |
| Recovery branch behind the PR head | Workflow fast-forwards it to the head |
| Recovery branch is a stale pre-rebase copy of the head | Workflow resets it to the head when the worktree is clean and every local commit already has a patch-equivalent on the head |
| Recovery branch has other local commits that diverge from the head | Workflow raises `WorktreeError` and hands the PR off; nothing is discarded |
| Stale worktree metadata | Loop prunes stale entries before creation |
| Orphan directory | Loop removes empty/link-only disposable orphans; preserves unknown user files |
| Two users of one named worktree | Workflow holds a repository/branch lock |
| Sandbox sync conflict | Not applicable: Loop currently has no isolated synchronization backend |

The Execution ledger records the batch's scope, each issue's attempt and the replies it drafted.
A clean publication failure can remove the directory safely: after a release the next run
recreates it from the retained branch and publishes the existing commit without another agent
call. A dirty failure keeps the preserved directory, and Loop raises `WorktreeError` when a scope
ends with retained edits; the command records the run as handed off. Unverified
edits and unexpected commits are never silently reset, committed, or discarded.

Inspect retained work before `--retry`. Repair or remove only the failed issue's
uncommitted changes, preserving prior verified commits. An interrupted commit is
reconciled using the actual parent and subject; an incomplete verification/commit
without a recognized commit is held for inspection.

Observed feedback is separate from handled feedback. Resolved/answered threads are
skipped; reviewer follow-ups and unanswered self-notes or `fix!:` comments become
pending. A new SHA alone does not reopen handled comments. Replies posted just before
a crash are reconciled against GitHub, and stale drafted replies do not answer newer
reviewer follow-ups. Repository collaborators, the operator, and known review bots
are accepted; unrelated authors are ignored.

State is the Execution ledger (one append-only JSONL log and lock per pull request) next to the
discovery baseline and optional Jenkins admission journal:

```text
.loop/pr-babysit/
    scheduler.json
    scheduler.lock
    ledger/pr_babysit/<owner>-<repo>-<number>-<hash>/log.jsonl
    ledger/pr_babysit/<owner>-<repo>-<number>-<hash>/lock
    jenkins/<hash>.json                   # head-bound builds and unresolved submission
    jenkins/<hash>.lock
    worktrees/babysit/owner/repo/123/        # default; `worktrees_dir` moves it elsewhere
```

Keep state outside tracked files. Writes use file fsync; a torn last line is ignored.
OS locks release on process exit. Changing discovery author/repositories requires a new
state directory or migration. One PR's blocker does not stop others. Shutdown
requests a shared stop, wakes poll waits, and joins owned PR threads before
releasing the scheduler lock. Unfinished work and local recovery branches remain available.
Unexpected scheduler errors request the same cooperative shutdown. There is no per-worker
terminate/kill fallback: in-flight actions must return before their threads can exit.
Agent and check timeouts still apply, but some Git operations have no timeout, so shutdown
has no guaranteed upper bound. Ordinary worker exceptions are isolated per PR; a fatal
process crash affects every PR thread.

### Adopting the earlier per-PR state

The earlier version kept `prs/<owner>--<repo>--<number>.json` (version 1). On the first run
for a pull request that state is adopted once into its ledger, under the work item's lock:
an open batch becomes an open address scope (finished issues are passed attempts with their
replies kept, an interrupted commit a committing attempt that is adopted or held for
inspection), rerun budgets, Copilot requests and handled feedback carry over, and a blocked
status keeps its fingerprint. The old file is left in place and never read again.

## Telemetry and validation

Discovery, dispatch, state access, and scheduler waits produce local logs only; the
scheduler creates no observer. Each PR thread builds its own services and observer,
and each submitted run starts with an empty context without changing process-wide trace
headers. Owned telemetry providers are flushed and closed when the run exits. Each
run exports an independent `pr_babysit` root with its scope and Stage attempt spans,
shared with agent observations; a poll that does nothing exports no span and writes
nothing to the ledger. Retained worktrees end their active telemetry span without claiming
disk removal.

```sh
python3 -m pytest workflows/tests/software_factory/workflows/pr_babysit
python3 -m pytest
```

Tests use fake GitHub/Jenkins/agent responses, isolated local Git remotes, temporary
state, and in-memory spans. They never invoke live agents or contact GitHub/Jenkins.

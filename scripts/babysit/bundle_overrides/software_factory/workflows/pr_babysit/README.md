# PR babysitting

This user-owned Workflow maintains one explicit PR through review and CI.
It uses only Loop's public API and is not included in the installed `loop` package.
Run from this checkout.

Native polling uses [novotnyllc/babysit-pr](https://github.com/novotnyllc/babysit-pr)
as a behavioral reference, without installing, pinning, vendoring, or wrapping it.
Review handling runs the `address` skill: its discussion gate decides when to trigger, and the
address agent follows its procedure (see *Review discussion* below).

## Structure

`pr_babysit` is one declarative definition over the factory's Stage library, run by
Loop's Workflow runtime once per pull request. Git, GitHub and process helpers come from Loop (`loop.shared`); state,
pacing and telemetry infrastructure from [`loop/src/software_factory/infrastructure`](../../../loop/src/software_factory/infrastructure); PR Stages are in
[`software_factory/src/pull_requests/stages`](../../src/pull_requests/stages) and the address-feedback activity is the
shared fragment in [`software_factory/src/pull_requests/flows`](../../src/pull_requests/flows).

```text
pr_babysit/
    definition.py                # the definition: validate origin, then poll until the pull request closes
    policy.py                    # fingerprint, commit subjects, what a blocked run records
    settings.py, settings.example.json
    intake.py                    # explicit PR identity and recovery from the Execution ledger
    legacy.py                    # one-time adoption of the earlier per-PR JSON state into the ledger
    __main__.py                  # CLI, services built by hand, console and OpenTelemetry spans
```

On every poll the definition does exactly one of: repair a confirmed merge conflict,
address the review discussion the address gate reports
(clean tree, baseline check, address agent, clean tree), maintain
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
a fresh worktree. Set `author` to override the expected PR author.

```sh
python3 -m software_factory.workflows.pr_babysit --config babysit.local.json --pr https://github.com/owner/repo/pull/123
```

The [babysit skill](../../../skills/babysit/SKILL.md) launches this exact `--pr`
command in a dedicated background supervisor. Each invocation targets one PR;
to watch multiple PRs, launch the skill separately for each.

```sh
python3 skills/babysit/scripts/start.py https://github.com/owner/repo/pull/123 --config babysit.local.json
```

There is no repository scan or PR discovery. The default poll interval is
60 seconds. Merged/closed PRs finish; green CI is a milestone, so open PRs
continue watching for late feedback.

Draft PRs are only watched. Once marked ready, it repairs conflicts, CI and
review threads, including threads left while it was a draft.
Jenkins recovery runs only when `jenkins.enabled` is `true`.

`--pr` runs one PR directly. A run that ends blocked is *handed off* with the pull
request's fingerprint recorded on the run, and while it stays blocked no agent runs and
nothing is pushed for it. The skill supervisor checks the same PR again;
on a changed fingerprint (head, base revision, review, or CI inputs), the next
invocation releases the blocked run. For an unchanged blocker or an interrupted
run that needs manual recovery, inspect the retained work and release the
specific ledger entry explicitly before restarting.
Pressing Ctrl+C (or SIGTERM) stops a run after its current poll and exits 130.

For telemetry, install `loop[otel]` and configure `otel_endpoint`, for example
`http://localhost:4318`. Without it no exporter is created. With it, INFO+ log records
are also exported over OTLP (`/v1/logs`) as `loop-babysit`. `copilot_review` defaults
to false; enabling it requests a best-effort Copilot review once per SHA. Pending
review prevents the readiness milestone, while unavailable Copilot does not block work.

## Responsibilities

| Actor | Responsibility |
|---|---|
| Loop library | Create/reuse named worktrees; keep them across polls and fast-forward them to origin; preserve dirty work; retain branch commits; prune stale metadata |
| Address | Run the `address` skill procedure in babysit's worktree: group threads, investigate with evidence, fix, commit, push, reply, resolve settled agent threads |
| Maintainer | Diagnose CI read-only, fix branch defects, identify transient failures, resolve merge conflicts |
| Factory runtime | Own the PR worktree, run the address gate, check what the address agent left, verify and publish CI fixes, retry CI, record every attempt in the Execution ledger, serialize one pull request with a lock |

The role agents' profiles and complete Markdown instructions live under [`software_factory/src/pull_requests/agents`](../../src/pull_requests/agents).
Maintainer must not commit, push, change branches, or use GitHub clients. Native deny
rules reinforce the prompts, and Python checks actual HEAD changes. The address agent
is the exception: it runs with default Copilot permissions and commits, pushes
(`git push origin HEAD:refs/heads/<headRef>`, never forced), replies and resolves itself,
but must not create, switch or remove branches or worktrees. Host agent execution is
not a sandbox. No code merges PRs. Python commits and publishes Maintainer's CI fixes. Three GitHub Actions transient retry cycles per SHA are allowed by default; repeated
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
`timeout` bounds each request; `log_bytes` bounds the console tail (1 KiB to 256 MiB,
default 64 MiB). Failed CI and Jenkins logs are saved to `$TMPDIR/babysit-logs/`; the
maintainer gets the file path, not the log, in its prompt and searches it. Credentials
never follow redirects.

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
waits. Restarting the process or disabling/re-enabling Jenkins does not erase this state.
Keep this directory alongside the Execution ledger; workers must share it for
cooperative locking. Independent human/SCM triggers cannot be globally capped
by the Workflow.

### Merge conflict recovery

Python pins the PR head, recovery head and the base branch tip freshly fetched from
origin (the PR payload's `base.sha` can lag it) in the ledger, then starts
`git merge --no-commit --no-ff <base-sha>` in the clean recovery worktree.
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
after inspection and explicit ledger release, it is verified again and published without another
resolver call. Dirty or ambiguous recovery work is never discarded automatically.

New blocked fingerprints include the base ref/SHA. Older blocked records retain their
previous comparison until another head/review/CI input changes or the ledger is released;
a base-only change cannot release a record that never observed its earlier base.

## Worktree lifecycle and recovery

Every PR uses a persistent local branch named after the PR head branch through
Loop's built-in `BranchStrategy(keep=True)`: the worktree is created once, reused by every
poll and run, fast-forwarded to `origin/<branch>` on each open, and removed by the
`remove-worktree` Stage only when the PR is closed or merged.
Worktree roots are explicitly configured absolute paths: `worktrees_dir` in the settings file
(resolved relative to it), by default `worktrees` under the state directory.

| Case | Result |
|---|---|
| Poll or run ends, PR still open | Loop keeps the directory; next open reuses it |
| Kept worktree clean and behind `origin/<branch>` | Loop fast-forwards it on open |
| PR closed or merged | `remove-worktree` removes a clean directory; a dirty one is kept with a warning; the branch remains |
| Recovery branch behind the PR head | Workflow fast-forwards it to the head |
| Recovery branch is a stale pre-rebase copy of the head | Workflow resets it to the head when the worktree is clean and every local commit already has a patch-equivalent on the head |
| Recovery branch has other local commits that diverge from the head | Workflow raises `WorktreeError` and hands the PR off; nothing is discarded |
| Stale worktree metadata | Loop prunes stale entries before creation |
| Orphan directory | Loop removes empty/link-only disposable orphans; preserves unknown user files |
| Two users of one named worktree | Workflow holds a repository/branch lock |
| Sandbox sync conflict | Not applicable: Loop currently has no isolated synchronization backend |

The Execution ledger records each scope, Stage attempt and the Events they produced.
A clean publication failure can remove the directory safely: after a release the next run
recreates it from the retained branch and publishes the existing commit without another agent
call. A dirty failure keeps the preserved directory, and Loop raises `WorktreeError` when a scope
ends with retained edits; the command records the run as handed off. Unverified
edits and unexpected commits are never silently reset, committed, or discarded.

Inspect retained work before releasing its ledger entry. Repair or remove only the failed issue's
uncommitted changes, preserving prior verified commits. An interrupted commit is
reconciled using the actual parent and subject; an incomplete verification/commit
without a recognized commit is held for inspection.

### Review discussion

Every poll of an open PR runs the address gate,
[`discussion_state.py`](../../src/pull_requests/infrastructure/github/pull_requests/discussion_state.py),
a copy of the `address` skill's `scripts/pr_discussion_state.py`. It reads unresolved review
threads (conversation comments and review bodies are not handled) and classifies them by
hidden markers: an opening `<!-- agent:comment` block makes a thread agent-owned; published
`<!-- agent:reply` blocks separate answered work from new `pending` work and agent threads to
`reconcile`. Login is never used for origin.

| Gate `action` | Babysit |
|---|---|
| `skip` | No address run; continue to CI, Copilot or readiness |
| `stop` (truncated thread, fetch error) | Block the PR with the gate's reason |
| `proceed` | Trigger the address agent, unless an earlier address run left the discussion in exactly this state |

The discussion state is identified by a token over thread ids, states and published comment
ids/`updatedAt` (not the head SHA). After a run the gate is read again and both tokens are
recorded, so threads that only wait on a human do not retrigger every poll; a reviewer's new
comment changes the token and triggers again.

The address agent's prompt is [`instructions.md`](../../src/pull_requests/agents/roles/address/instructions.md),
the skill's `SKILL.md` body adapted to babysit (worktree supplied, no cleanup), rendered per run:
the PR, head, build/test commands and the unresolved discussion (the gate JSON, compacted) fill its
`{{...}}` placeholders. The agent loads the installed `review` skill (`references/investigation.md`), `github`
skill (Reviews: draft, reply, resolve) and `write` skill (`github` route) itself; nothing is appended.
The prompt is one command-line argument, so diff hunks (outdated threads keep theirs), then comment
bodies are shed when it nears 120 KB; the agent can always rerun the gate for the full form.
The run fails, blocking the PR, when the agent reports `blocked`, leaves commits unpushed, leaves
a pending thread unanswered on the same latest comment, or leaves the worktree dirty.

State is the Execution ledger (one append-only JSONL log and lock per pull request)
and optional Jenkins admission journal:

```text
.loop/pr-babysit/
    ledger/pr_babysit/<owner>-<repo>-<number>-<hash>/log.jsonl
    ledger/pr_babysit/<owner>-<repo>-<number>-<hash>/lock
    jenkins/<hash>.json                   # head-bound builds and unresolved submission
    jenkins/<hash>.lock
    worktrees/<head branch>/        # default; `worktrees_dir` moves it elsewhere
```

Keep state outside tracked files. Writes use file fsync; a torn last line is ignored.
OS locks release on process exit. Independent skill instances use per-PR locks;
one PR's blocker does not stop others. Shutdown requests a stop and wakes poll
waits. Unfinished work and recovery branches remain available. Agent and check
timeouts still apply, but some Git operations have no timeout, so shutdown has
no guaranteed upper bound.

### Adopting the earlier per-PR state

The earlier version kept `prs/<owner>--<repo>--<number>.json` (version 1). On the first run
for a pull request that state is adopted once into its ledger, under the work item's lock:
an open CI batch becomes an open fix scope, an open address batch is dropped so the address
gate decides afresh, rerun budgets and Copilot requests carry over, and a blocked
status keeps its fingerprint. The old file is left in place and never read again.

## Telemetry and validation

The explicit invocation builds its own services and observer. Owned telemetry
providers are flushed and closed when the run exits. Each run exports an
independent `pr_babysit` root with its scope and Stage attempt spans,
shared with agent observations; a poll that does nothing exports no span and writes
nothing to the ledger. Retained worktrees end their active telemetry span without claiming
disk removal.

```sh
python3 -m pytest software_factory/tests/workflows/pr_babysit
python3 -m pytest
```

Tests use fake GitHub/Jenkins/agent responses, isolated local Git remotes, temporary
state, and in-memory spans. They never invoke live agents or contact GitHub/Jenkins.

`scripts/tests/fixtures/jenkins/pr-536-build-5/` holds a recorded real Jenkins failure
(`history.json` job API response, `console.txt` plain console, `meta.json` job/head/size).
`scripts/tests/test_jenkins_logs.py` replays it through the `transport` seam; run from
`scripts/` with `python3 -m pytest tests`.

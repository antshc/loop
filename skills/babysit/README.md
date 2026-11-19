# Babysit settings

JSON config; select with `--config PATH`, `LOOP_BABYSIT_CONFIG`, or `.loop/pr-babysit.json`. Relative paths resolve against the config file's folder. Source: [settings.py](scripts/software_factory/workflows/pr_babysit/settings.py), example: [settings.example.json](scripts/software_factory/workflows/pr_babysit/settings.example.json).

## Top level

| Key | Default | Description |
|---|---|---|
| `repositories` | required | Non-empty list, unique `name`s. PR repo MUST be listed. |
| `state_dir` | `.loop/pr-babysit` | Ledger, locks, run logs, Jenkins state. |
| `worktrees_dir` | `<state_dir>/worktrees` | Where PR worktrees are created. |
| `author` | `""` | Overrides expected PR author. |
| `interval` | `60` | Seconds between polls; `> 0`. |
| `retries` | `3` | Transient-CI rerun budget per head; `>= 0`. |
| `timeout` | `1800` | Seconds per agent/command run; `> 0`. |
| `copilot_review` | `false` | Request one best-effort Copilot review per head SHA; a pending review blocks readiness, unavailable Copilot does not. |
| `otel_endpoint` | `null` | OTLP/HTTP base URL, e.g. `http://localhost:4318`. Exports traces, metrics and INFO+ Python logs (as `loop-babysit`). `null` = no exporter. Local endpoint with no listener: `start.py` starts a shared detached Aspire dashboard (`aspire dashboard run`, UI `http://localhost:<aspire_dashboard_port>`, log `<state_dir>/aspire/dashboard.log`) and waits until it accepts OTLP; remote unreachable = launch fails. Still unreachable when the supervisor starts = one warning, no exporter. |
| `otel_capture_content` | `true` | With `otel_endpoint`: records agent prompt (incl. agent instructions), output, and tool arguments/results on spans (`gen_ai.input.messages` etc.). `false` = metadata only. |
| `aspire_dashboard_port` | `19000` | Aspire dashboard UI port (`http://localhost:19000`) when `start.py` starts the dashboard; 1-65535. |
| `harness_dir` | `null` | Existing folder where the agent CLI starts; it then changes into the PR worktree. Unset = starts in the worktree. |
| `jenkins` | `null` | Jenkins CI integration; see Jenkins. |

Unknown keys (e.g. `concurrency`) are ignored.

## `repositories[]`

| Key | Description |
|---|---|
| `name` | `owner/repo`; MUST match the PR's repo. |
| `path` | Local checkout; its `origin` MUST match `name`. |
| `build` | Argv array, no shell. Run as baseline check; include dependency prep for fresh worktrees. |
| `test` | Argv array, no shell. |
| `ignored_checks` | Optional array of case-sensitive globs (`fnmatch`), default `[]`. Matching check runs (by name) and commit statuses (by context) are dropped from the CI state; a failed workflow run is dropped when its name matches or all its failed jobs match. Ignored failures do not trigger CI repair, and readiness accepts GitHub's `unstable` state when ignored checks failed. E.g. `["*/ secret-scanner"]`. |

## Behavior

Draft PRs: watch only. Ready PRs: repairs conflicts, CI and review threads (incl. threads left while draft). Pushes commits to the PR branch.

Temporary GitHub state-read connection failures, timeouts and HTTP 500/502/503/504 responses log a warning and retry after `interval`, retaining the monitor lock. SIGTERM interrupts the wait. Access refusals and other permanent errors still stop the supervisor. A handoff caused by a temporary read-only `gh api user` failure resumes after a successful snapshot, even without a PR change; other unchanged blocked work stays blocked.

In the Loop checkout, bundle-specific files are maintained under `scripts/babysit/bundle_overrides/`; `.deleted` lists source paths intentionally absent from the bundle. The synchronizer applies these overrides before checking or generating the skill.

## `jenkins`

Stages run only when `enabled` is `true`; otherwise Jenkins is skipped.

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Boolean. `false`/absent = Jenkins integration off. |
| `url` | required | HTTPS origin only: no path, query, userinfo. |
| `job_roots` | required | Non-empty list of trusted job-folder paths starting `/job/` and ending `/`. |
| `credentials_file` | required | JSON with `jenkinsUser` + `jenkinsApiToken`; keep out of git. |
| `ca_bundle` | `null` | CA bundle path; exclusive with `insecure_tls`. |
| `insecure_tls` | `false` | Skip TLS verification. |
| `timeout` | `30` | Seconds per request; `0 < t <= 3600`. |
| `log_bytes` | `67108864` | Max console log bytes read; `1024..268435456`. |

## Gates

A gate is the verdict check a Stage runs on its own result: Pass, Hold (stop, needs a human or another route) or Fail. Paths are under `scripts/software_factory/src/pull_requests/`.

- `stages/pull_requests.py` `_origin_matches`: Holds unless the checkout's `origin` is the PR's repository.
- `stages/pull_requests.py` `_accepted`: Fails when publication was refused (PR closed, branch changed, head moved, pushed SHA unconfirmed).
- `stages/checks.py` `_checked`: Passes on green build+test; red Holds if PR CI is failing too, else Fails.
- `stages/ci.py` `_diagnosed`: Maps the agent's CI classification to `Transient` or `BranchBroken`; anything else Holds.
- `stages/ci.py` `_reran`: Fails when no failed workflow run was rerun.
- `stages/ci.py` `_fixed`: Fails unless the agent reports `fixed` and actually changed files.
- `stages/conflicts.py` `_resolved`: Requires HEAD unchanged and the merge still in progress, then Fails unless the agent reports `fixed`.
- `stages/conflicts.py` `verify` (inline gate): Passes the verified merge tree, forwards a `Fail` from the build/test checks.
- `stages/jenkins.py` `_diagnosed`: Maps the Jenkins failure classification to `Transient` or `BranchBroken`; anything else Holds.
- `stages/jenkins.py` `_readiness`: Tracked Jenkins job `failed` Fails, `waiting` gives `JenkinsWaiting`, `green` gives `OtherCi`, else Holds.
- `stages/address.py` `_addressed`: Fails when the agent is blocked, left commits unpushed, the address gate says `stop`, or a pending thread is still unanswered.
- `infrastructure/github/pull_requests/discussion_state.py` `gate`: Review-thread gate, run on every poll; reports `proceed` (actionable threads) or `stop` (error folded into the reason); `discussion_token` identifies the state so an already-addressed one is not repeated.
- Pass-through gates, always Pass: `stages/copilot.py` `_recorded`, `stages/readiness.py` `_reported`, `stages/pacing.py` `_waited`, and inline gates of `snapshot`, `decide_next`, `block_pr`, `start`, `plan`, `prepare`, `publish` (merge), `observe`, `restart`, `capacity`.

## Stages

Paths are under `scripts/software_factory/src/pull_requests/`; stage name in parentheses.

- `stages/pull_requests.py` `validate_origin` (`validate-origin`): Holds the run unless the checkout belongs to the PR's repository.
- `stages/pull_requests.py` `snapshot` (`snapshot`): Reads the PR, CI and review state plus the address gate JSON into one `PullRequestPolled` event.
- `stages/pull_requests.py` `decide_next` (`decide-next`): Pure; picks the one action of the poll: closed, unsupported, conflict, pending discussion, failing CI, Copilot review, readiness.
- `stages/pull_requests.py` `block_pr` (`block-pr`): Hands the PR off as blocked, recording the reason and a fingerprint that includes our own replies.
- `stages/pull_requests.py` `publish_batch` (`publish-batch`): Pushes the CI fix without force and confirms the remote head equals it.
- `stages/pacing.py` `polls` (`polls`): Selector yielding an endless sequence of numbered polls.
- `stages/pacing.py` `start` (`start-poll`): Records the poll number.
- `stages/pacing.py` `pace` (`pace`): Waits `interval` seconds, or until a stop signal.
- `stages/workspaces.py` `pr_worktree` (`pr-worktree`): Workspace opener giving a kept worktree on the PR head branch, fast-forwarded to origin and aligned to the polled head.
- `stages/workspaces.py` `remove_worktree` (`remove-worktree`): On PR close or merge, removes the kept worktree if clean; a dirty one is kept.
- `stages/checks.py` `run_checks` (`run-checks`, `baseline`): Runs the repository build then test command on the worktree.
- `stages/address.py` `address_discussion` (`address-discussion`): Address agent fixes, commits, pushes, replies and resolves pending review threads.
- `flows/address.py` `address_feedback` (`address`): Fragment: clean tree, baseline checks, address agent, clean tree again.
- `stages/conflicts.py` `plan` (`plan-merge`): Pins base and head revisions before the merge starts.
- `stages/conflicts.py` `prepare` (`prepare-merge`): Starts a non-committing merge of the base into the PR branch.
- `stages/conflicts.py` `resolve` (`resolve-conflicts`): Maintainer agent edits the conflicted files only.
- `stages/conflicts.py` `verify` (`verify-merge`): Runs build+test on the staged merge tree and checks it did not change.
- `stages/conflicts.py` `publish` (`publish-merge`): Commits the verified merge and pushes it, adopting an interrupted identical commit.
- `stages/ci.py` `diagnose` (`diagnose-ci`): Read-only maintainer agent classifies failed CI as transient or branch-related, once per head and CI-run signal.
- `stages/ci.py` `rerun_failed` (`rerun-failed`): Reruns failed GitHub Actions jobs, capped by `retries` per head.
- `stages/ci.py` `fix` (`fix-ci`, `fix-jenkins`): Maintainer agent repairs the branch; the runtime verifies and commits.
- `stages/jenkins.py` `observe` (`observe-jenkins`): Picks the CI provider: Jenkins failed, Jenkins waiting, or other CI.
- `stages/jenkins.py` `diagnose` (`diagnose-jenkins`): Read-only maintainer agent classifies a failed Jenkins build from its console log.
- `stages/jenkins.py` `restart` (`restart-jenkins`): Resubmits the failed Jenkins job once, reconciled after interruption.
- `stages/jenkins.py` `capacity` (`jenkins-capacity`): Rechecks the current head and Jenkins capacity before fixing or publishing.
- `stages/jenkins.py` `readiness` (`jenkins-readiness`): Blocks readiness until a tracked Jenkins job has built the new head.
- `stages/copilot.py` `request_review` (`request-copilot-review`): Requests one Copilot review per head; refusal is recorded as unavailable.
- `stages/readiness.py` `report` (`report-readiness`): Reports ready or not ready once per head and readiness.
- `workspaces.require_clean` (Loop library; `require-clean-*`): Holds when the worktree is dirty.

## Agents

Paths are under `scripts/software_factory/src/pull_requests/agents/roles/`; each agent is run by the stage in parentheses and returns one JSON reply that Python parses. Agents never merge, rebase or force-push.

- `maintainer/__init__.py` `DIAGNOSE_CI` (`stages/ci.py` `diagnose`): Read-only; classifies failed GitHub Actions logs as `branch`, `transient` or `blocked`.
- `maintainer/__init__.py` `DIAGNOSE_JENKINS` (`stages/jenkins.py` `diagnose`): Read-only; classifies a failed Jenkins console log and a category that must match the classification (`timeout`, `flaky_test`, `container_error`, `likely_transient` = transient; `test_failure`, `branch_defect` = branch; `unknown` = blocked).
- `maintainer/__init__.py` `FIX_CI` (`stages/ci.py` `fix`): Applies the smallest change for the diagnosis and replies `fixed` or `blocked`; Python runs build+test, commits and pushes.
- `maintainer/__init__.py` `RESOLVE_CONFLICTS` (`stages/conflicts.py` `resolve`): Edits only the conflicted paths of the merge Python started, keeping both branches' intent; Python stages, verifies, commits and pushes.
- `address/__init__.py` `ADDRESS` (`stages/address.py` `address_discussion`): Runs the `review` skill's `address` procedure on pending review threads; fixes, commits, pushes, replies and resolves settled agent threads itself, then replies `addressed`, `waiting` or `blocked`.

Maintainer agents (`maintainer/instructions.md`) MUST NOT commit, push, post comments, resolve threads or use GitHub clients, and only edit the supplied worktree. The address agent (`address/instructions.md`) runs with full Copilot permissions and owns commits, push, replies and thread resolution; its instructions adapt the `review` skill's `address` route and load the installed `review`, `github` and `write` skills for investigation, reply wording, posting and resolution. Both roles time out after 1800s unless `timeout` overrides it.

## Flow

Source: [definition.py](scripts/software_factory/workflows/pr_babysit/definition.py), [\_\_main\_\_.py](scripts/software_factory/workflows/pr_babysit/__main__.py). Jenkins branches apply only with `jenkins.enabled`.

```mermaid
%%{init: {'themeVariables': {'lineColor': '#8b949e'}}}%%
%% diagram-id: pr-babysit-flow-swimlane
swimlane-beta TB
  accTitle: Babysit flow
  accDescr: Supervisor starts the pr_babysit workflow, which polls the pull request and hands each decided action to agents, GitHub or Jenkins until the PR closes or blocks.

  subgraph sup [Supervisor]
    start([Start])
    lock[1 - Lock PR and check it is open]
    spawn[2 - Spawn workflow process]
    check{10 - PR closed, or status not waiting?}
    exitSup([Exit])
    sleep[10b.1 - Sleep interval]
    supLoop([Next cycle - back to step 2])
  end

  subgraph wf [Workflow - pr_babysit]
    init[3 - Load settings, adopt old state, release blocked PR if GitHub inputs changed]
    origin{4 - Origin matches PR repository?}
    holdOrigin[4a.1 - Hold run]
    poll[5 - Start poll and request snapshot]
    decide{7 - Decide next action}
    holdClosed[7a.1 - Hold run, PR closed]
    cPrep[7c.1 - Plan and prepare merge]
    cVerify[7c.3 - Verify merge with build and test]
    cPublish[7c.4 - Commit merge]
    dBase[7d.1 - Check clean tree and baseline build and test]
    dAfter[7d.4 - Recheck address gate and clean tree]
    eJenkins{7e.1 - Tracked Jenkins job failed?}
    eClass{7e.3 - Failure classification?}
    eRerun[7e.3a.1 - Rerun failed jobs or restart Jenkins job, capped by retries]
    eVerify[7e.3b.2 - Verify and commit fix]
    fCopilot[7f.1 - Request Copilot review once per head]
    gReady[7g.1 - Report readiness once per head]
    pace[8 - Pace interval]
    pollLoop([Next poll - back to step 5])
    block[7b.1 - Block PR, record fingerprint and reason]
    runEnd([Run ends])
  end

  subgraph ag [Agents - maintainer and address]
    cResolve[7c.2 - Resolve conflicted files]
    dAddress[7d.2 - Fix, commit, reply and resolve threads]
    eDiag[7e.2 - Diagnose failure as transient or branch-related]
    eFix[7e.3b.1 - Fix branch]
  end

  subgraph gh [GitHub]
    read[/6 - Read PR, checks, review threads, address gate/]
    eActionsLog[/7e.1b.1 - Read failed workflow logs/]
    cPush[7c.5 - Push merge commit]
    dPush[7d.3 - Push commits and thread replies]
    ePush[7e.3b.3 - Push fix]
    fReq[7f.2 - Copilot review requested]
  end

  subgraph jk [Jenkins]
    eJenkinsLog[/7e.1a.1 - Read failed build log/]
  end

  start --> lock --> spawn --> init --> origin
  origin -->|no| holdOrigin --> runEnd
  origin -->|yes| poll --> read --> decide
  decide -->|closed| holdClosed --> runEnd
  decide -->|unsupported author or fork, address gate stop| block
  decide -->|merge conflict| cPrep --> cResolve --> cVerify --> cPublish --> cPush --> pace
  decide -->|pending review discussion| dBase --> dAddress --> dPush --> dAfter --> pollLoop
  dBase -->|red baseline and CI red too| eJenkins
  decide -->|CI failing| eJenkins
  eJenkins -->|yes| eJenkinsLog --> eDiag
  eJenkins -->|no| eActionsLog --> eDiag
  eDiag --> eClass
  eClass -->|transient| eRerun --> pace
  eClass -->|branch| eFix --> eVerify --> ePush --> pace
  eClass -->|unclear| block
  decide -->|Copilot due| fCopilot --> fReq --> pollLoop
  decide -->|otherwise| gReady --> pace
  pace --> pollLoop
  cPrep -->|stage failed| block
  cVerify -->|stage failed| block
  dBase -->|stuck| block
  dAddress -->|stuck| block
  block --> runEnd
  runEnd --> check
  check -->|PR closed, or other status| exitSup
  check -->|handed off, held or idle| sleep --> supLoop

  classDef default fill:#242424,stroke:#8b949e,color:#c9d1d9,stroke-width:1px
```

# Unified event-driven SDLC workflow

Status: Proposed implementation plan
Date: 2026-10-24
Repository: antshc/loop

## Goal

Build one durable workflow driven by messages from users, GitHub issues, pull
requests, agents, CI, and delivery monitoring. One workflow instance follows each
work item through triage, specification, implementation, review, verification,
human approval, delivery, and follow-up.

The developer accepts tasks from a spec ticket, a direct user request, or a
justified fix request from Address or Maintainer. Existing dev and PR babysitting
capabilities become stages and handlers in this unified workflow.

This document consolidates the event-system plan, technical stack, supplied
decision-flow screenshots, and GitHub user triggers. It describes planned behavior;
the new commands and event runtime are not implemented yet.

## Current integration points

- [Dev entry point](../../workflows/dev/run.py): dependency wiring, backlog
  processing, fake dependencies, logging, and telemetry.
- [Dev phase definition](../../workflows/dev/definition.py): planning, ticket
  implementation, publication, verification, review, and readiness reporting.
- [PR babysitting](../../workflows/pr_babysit/README.md): discovery, review handling,
  CI maintenance, JSON checkpoints, process locks, and publication recovery.
- [PR orchestration](../../workflows/pr_babysit/workflow/run.py): continuous
  observation and dispatch to Address/Maintainer.
- [PR state](../../workflows/pr_babysit/infrastructure/state.py): durable JSON
  records and OS process locks.
- [Loop usage](../../USAGE.md): reusable profiles, AgentRequest, worktree execution,
  optional WorkflowBuilder reporting.

Today Address and Maintainer can edit code. The target responsibility change is
to have them diagnose/propose fixes and delegate code changes to the shared
developer. Existing dev commit behavior must also be aligned with workflow-owned
verification and publication before both paths share the new handlers.

## Architecture decisions

1. One workflow definition; one persistent instance per work item.
2. Python owns transitions, routing, policy, verification, and external effects.
3. Agents return structured decisions/results; Python validates them before
   scheduling another agent.
4. Human decisions are persistent waiting states. Waiting does not hold a worker
   or keep an agent session alive.
5. Events describe facts; commands request actions with one logical handler.
   Both use a common message envelope.
6. GitHub issues hold scope, questions, specifications, and decisions. PRs hold
   implementation, review discussion, and verification references.
7. Internal workflow state, deliveries, retries, and execution journals live in
   the local durable store.
8. Loop continues to own agent execution and worktree lifecycle. The user-owned
   workflow owns GitHub policy, branch serialization, explicit commit/push,
   publication, and business decisions.
9. WorkflowBuilder reports phases and telemetry; durable transition behavior is
   implemented by the workflow runtime.
10. The initial deployment is a single host with bounded concurrent workers.
    Cross-host workers and an external broker are deferred.

## Decision flow with user triggers

```text
USER: Creates GitHub issue or submits a direct request
  |
  v
TRIAGE AGENT
  +-- Needs clarification
  |     +-- Ask in issue -> USER replies -> Triage again
  |
  +-- Park
  |     +-- Wait -> USER requests resume -> Triage again
  |
  +-- Needs specification
  |     +-- SPEC AGENT -> Write spec in issue
  |           +-- USER requests changes -> Revise spec
  |           +-- USER approves spec -> Developer
  |
  +-- Ready to implement -> Developer
        |
        v
DEVELOPER AGENT
  +-- Implement -> Workflow verifies -> Commit/push -> Create/update PR
        |
        v
PR REVIEW + CI
  +-- USER requests changes or review agent reports findings
  |     +-- ADDRESS AGENT
  |           +-- Fix needed -> Developer -> Push -> Review + CI
  |           +-- Clarification needed -> USER replies -> Address
  |           +-- No fix needed -> Explain in PR -> Re-evaluate readiness
  |
  +-- CI fails
  |     +-- MAINTAINER AGENT
  |           +-- Code defect -> Developer -> Push -> Review + CI
  |           +-- Transient failure -> Retry CI
  |           +-- Blocked -> USER provides input -> Resume
  |
  +-- Review completed + CI green + verification evidence
        |
        v
USER: Reviews PR and evidence
  +-- Requests changes -> Address agent
  +-- Approves current revision
        |
        v
MERGE GATE: Required checks and approvals pass
  +-- USER merges PR, or explicitly enables auto-merge
        |
        v
CI/CD -> Deployment
  +-- Failure -> Report in linked issue -> Triage
  +-- Success -> Record delivery -> Close completed issue
        |
        v
MONITOR DELIVERY AND PRODUCT
  +-- Issue detected -> Create linked issue -> Triage
  +-- No issue -> Continue monitoring
```

Monitoring may continue after an individual work item completes. New findings
create linked work items, rather than reopening every delivered workflow.

## Agent and workflow responsibilities

| Actor | Responsibility |
|---|---|
| Triage | Decide implement, specify, clarify, or park; explain decision |
| Spec | Produce/revise scope, acceptance criteria, and unresolved questions |
| Developer | Implement a new task or requested code fix |
| Code reviewer | Inspect changes and produce evidence-backed findings |
| Address | Investigate review feedback; propose justified fixes; draft replies |
| Maintainer | Diagnose CI failure; distinguish code defects, infrastructure failures, and transient failures |
| Verifier | Execute/interpret feature checks and collect evidence |
| Monitor | Identify actionable delivery/product issues |
| Python workflow | Validate results, transition state, schedule work, run required checks, commit/push, post replies, retry CI, persist progress |
| Human | Resolve ambiguity, approve specs and code/evidence, authorize merge or auto-merge |

Address and Maintainer MUST delegate code fixes through the shared developer task
contract. Agents MUST NOT bypass workflow policy by directly invoking another
agent or publishing changes. A successful agent process is not proof that the task
or verification succeeded.

## GitHub and direct-user triggers

Commands below are proposed exact comment commands. Accept them from configured
authorized actors only. Use GitHub review actions for review approval/changes.

| User action | Message | Workflow action |
|---|---|---|
| Open an eligible GitHub issue | TaskSubmitted | Create work item; triage |
| Submit direct user request | TaskSubmitted | Persist request; triage; create/link tracking issue before repository work |
| Comment /implement on issue | ImplementationRequested | Check scope/readiness; implement or request missing decisions |
| Comment /spec on issue | SpecificationRequested | Create/revise specification |
| Comment /approve-spec on issue | SpecApproved | Approve the current explicitly identified spec revision |
| Reply to a pending question | ClarificationProvided | Resume the waiting decision |
| Request changes on pending spec | SpecChangesRequested | Return to Spec with feedback |
| Submit PR review requesting changes | ReviewReceived | Schedule Address |
| New eligible review comment/follow-up | ReviewReceived | Schedule Address; deduplicate by comment revision |
| Comment /retry on issue or PR | RetryRequested | Reconcile existing effects and retry eligible blocked/failed work |
| Comment /pause | PauseRequested | Stop scheduling new work; let active work reach a safe checkpoint |
| Comment /resume | ResumeRequested | Reconcile current state; resume paused/parked work |
| Approve PR | PullRequestApproved | Record approval for reviewed commit; re-evaluate gates |
| Merge PR | PullRequestMerged | Track CI/CD and delivery |
| Explicitly enable auto-merge | AutoMergeEnabled | Let GitHub enforce required checks and approvals |
| Close issue or close unmerged PR | WorkCancelled | Stop new work; preserve recovery data |
| Reopen issue or PR | WorkReopened | Reconcile and re-triage or resume the appropriate state |

Ordinary issue comments provide context. Only explicit commands and replies
correlated with a pending question trigger issue workflow actions. PR review
comments and review submissions are explicit review inputs. Record question IDs,
actor identity, source object ID/revision, and the decision being answered.

For spec approval, show a version/hash in the approval request and associate the
approval with it. If the spec changed after the approval request, request fresh
approval. Reject ambiguous replies rather than treating every comment as consent.

Configure enrolled repositories and eligible issue/PR sources. On first scan,
baseline existing objects unless explicitly enrolled; do not start every historical
issue. Deduplicate polling and any future webhook deliveries against the same
logical source event. Ignore the workflow's own acknowledgement/status comments.

## Technical stack and durable bus

Initial stack: Python, standard-library sqlite3, SQLite on local disk, and the
existing Loop execution/public API. GitHub access reuses workflow adapters and
authenticated gh operations.

SQLite implements the durable queue and state store. A dispatcher claims ready
deliveries, executes a handler, and records the outcome. An optional asyncio.Queue
can wake a dispatcher inside one process; bounded database polling also discovers
cross-process work and recovers lost wake-ups.

This is an application queue implemented over SQLite. Delivery, leases, retry
policy, and consumer semantics must be implemented and tested explicitly.

Proposed storage:

| Table | Contents |
|---|---|
| work_items | Identity, state, version, issue/spec/branch/PR/deployment links |
| messages | Immutable versioned envelopes; source deduplication keys |
| deliveries | Message/handler pair, status, attempts, lease owner/expiry, next attempt |
| transitions | Previous/new state, triggering message, reason, timestamp |
| executions | Agent/workflow attempt, input revision, output/evidence references |
| approvals | Actor, decision, exact spec revision or commit SHA |
| effects | External-effect intent, idempotency key, checkpoints, confirmed result |
| source_cursors | Discovery baseline and last successfully persisted observations |

Persist the state transition and new messages in one short database transaction.
Claim deliveries atomically, with a unique message/handler key. Never hold a
database transaction open while running an agent, Git, tests, or network calls.

Use at-least-once delivery with idempotent handlers. Do not claim exactly-once
external effects: after a crash, reconcile Git commits, remote SHAs, PRs, comments,
and CI actions before retrying. Persist an effect intent before the external call,
then its confirmed outcome. Uncertain outcomes block or reconcile.

Workers heartbeat delivery leases. Lease expiry MUST NOT start a second writer
while an old agent process can still mutate the worktree. Hold an OS lock for the
whole branch mutation lifecycle and reconcile/stop orphan processes before retry.

PostgreSQL or a broker such as NATS can be evaluated if cross-host workers become
necessary. Preserve message/handler contracts behind a store interface. Replacing
transport alone does not provide distributed worktree locking or state consistency.

## Message and developer-task contracts

Common message envelope:

```text
message_id, schema_version, message_type, kind (event | command)
source, source_event_id, actor
work_item_id, correlation_id, causation_id
occurred_at, received_at
expected_work_item_version
payload
```

Developer task payload:

```text
work_item_id
task_kind: implementation | fix
instructions
acceptance_criteria
spec_reference + spec_revision
repository
branch + pull_request (when present)
expected_head_sha
evidence_references
caused_by_message_id
```

A fix keeps the original work-item, branch, and PR identity. It does not accidentally
create a new feature branch or duplicate PR. A new independent bug gets a linked
work item. One work item may contain child implementation tasks; parent approval
and readiness require completion of the relevant children.

Representative routing:

| Message | Handler/action |
|---|---|
| TaskSubmitted / WorkReopened | Triage or reconcile existing workflow |
| ClarificationProvided | Resume recorded pending decision |
| SpecApproved | Schedule developer after revision validation |
| FixRequested | Schedule developer for current branch/revision |
| ImplementationCompleted | Validate/publish candidate; schedule review and verification |
| ReviewReceived | Address |
| ChecksFailed | Maintainer |
| VerificationCompleted | Evaluate evidence and human-review readiness |
| PullRequestApproved | Re-evaluate merge readiness |
| PullRequestMerged | Track delivery |
| DeploymentFailed | Diagnose/report linked issue |
| IssueDetected | Create linked issue/work item; triage |
| WorkBlocked | Persist reason and request human input |

## State, gates, and concurrency

Suggested lifecycle states: received, triaging, awaiting_clarification, parked,
specifying, awaiting_spec_approval, implementing, reviewing, verifying,
awaiting_pr_approval, ready_to_merge, awaiting_merge, deploying, delivered,
blocked, paused, cancelled.

Keep review findings, CI checks, and verification results as separate revision-bound
records: they may arrive concurrently even if the work item has one lifecycle state.

- Validate every transition against current state, work-item version, and revision.
- Serialize transitions per work item and mutations per repository/branch/worktree.
- Permit bounded parallelism across independent work items.
- Queue incoming messages during execution; reconcile changed heads before further
  edits or publication.
- Bind approvals and checks to a spec revision or commit SHA. New commits invalidate
  relevant prior evidence/readiness; stale events cannot make a newer head ready.
- Issue-driven tasks may enter implementation only after triage readiness or approved
  specification. /implement is not permission to skip unresolved requirements.
- Require verified changes before explicit commit/push and confirmed publication
  before replies that claim a fix is available.
- Preserve GitHub-required review and CI gates. The initial workflow does not merge
  autonomously; the user merges or explicitly enables auto-merge.
- Record delivery separately from merge. Close the parent issue only when its
  configured completion criteria are met; avoid automatic merge-closing keywords
  if deployment success is required.
- Bound agent repair cycles and transient CI retries; escalate exhausted budgets.
- /retry does not erase unverified changes, override approvals, or reset budgets
  silently. /resume reconciles current GitHub and local state first.
- Pause is a scheduling pause, not an unsafe process kill. Cancellation stops new
  work and retains recovery branches and evidence.

## Worktree and publication recovery

Reuse Loop's existing worktree API. Do not introduce another worktree manager.

| Case | Required behavior |
|---|---|
| Failed run, clean worktree | Remove directory; retain local branch commits |
| Failed run, dirty worktree | Preserve directory and report recovery details |
| Successful body leaves edits | Treat as incomplete; no automatic commit |
| Temporary branch merged successfully | Loop merges into host and deletes temporary branch |
| Merge conflict/divergence | Preserve recovery branch; workflow resolves/escalates |
| Stale Git metadata | Loop prunes stale entries |
| Unknown orphan directory contents | Preserve; do not remove user files |
| Two workers target same branch | Workflow serializes through shared branch lock |
| Push rejected or uncertain | Reconcile remote state; no premature fixed reply |
| Crash after remote effect | Reconcile before retry to avoid duplicate PR/comment/action |
| Isolated sandbox sync conflict | Deferred: no such backend currently in Loop |

Named feature/PR branches remain the normal unified-workflow path. Temporary-branch
merge behavior stays available through Loop but does not replace PR approval gates.

## Proposed folder structure

Keep the workflow and its infrastructure grouped in one user-owned package:

```text
workflows/
  factory/
    __main__.py
    settings.py
    workflow/
      definition.py
      state.py
      messages.py
      transitions.py
      routing.py
      handlers/
        triage.py
        spec.py
        develop.py
        review.py
        address.py
        maintain.py
        verify.py
        deliver.py
        monitor.py
      workspace.py
      publication.py
    infrastructure/
      dispatcher.py
      sqlite_store.py
      migrations.py
      locks.py
      github.py
      git.py
      verification.py
      sources/
        user_request.py
        github_issues.py
        github_pull_requests.py
        ci.py
        deployments.py
    agents/
      profiles.py
      triage.md
      spec.md
      developer.md
      reviewer.md
      address.md
      maintainer.md
      verifier.md
      monitor.md
    tests/
      workflow/
      infrastructure/
      integration/
  dev/                 # Compatibility entry point during migration
  pr_babysit/          # Compatibility entry point during migration
```

Profiles and complete instructions stay together in agents/. Keep state outside
tracked files, for example .loop/factory/state.sqlite3, logs, evidence, and
worktree roots. Do not duplicate prompts or Git/worktree lifecycle implementations.

## Implementation phases

### 1. Contracts and decision flow

- Define typed messages, work items, decisions, commands, and result validation.
- Implement a deterministic transition function: state + message -> new state +
  commands/effect intents.
- Define invalid/stale-event behavior and all human waiting states.
- Add focused transition tests covering every branch in the decision flow.

### 2. Durable runtime

- Implement schema migrations, atomic claims, state updates, deliveries, retry
  scheduling, leases, effect journal, and source cursors.
- Add dispatcher startup/shutdown, branch locks, orphan detection, and reconciliation.
- Expose enqueue, inspect, pause, resume, and retry operations.
- Keep slow calls outside database transactions.

### 3. Developer path

- Adapt existing dev task execution behind the shared developer handler.
- Align commit responsibilities with Python-owned verification/publication.
- Accept direct requests and issue/spec references through the same task contract.
- Preserve dry/fake seams and reuse existing profiles/execution APIs.
- First vertical slice: request -> triage -> developer -> verified draft PR.

### 4. PR review and CI

- Turn existing babysitter polling into message ingress and finite handlers.
- Reuse feedback grouping, fingerprints, publication checkpoints, and reply recovery.
- Refactor Address/Maintainer to request developer fixes.
- Review and CI messages resume the same work item/branch.
- Preserve late-feedback handling, author/fork checks, per-SHA retry behavior,
  and reviewer-owned thread resolution.

### 5. Specification and human gates

- Add triage/spec profiles and revisioned spec publication.
- Parse authorized user commands and correlate pending-question replies.
- Persist waiting/parked/paused states and resume without holding workers.
- Bind approvals to exact revisions and refresh readiness after head changes.

### 6. Delivery and monitoring

- Track merge, CI/CD execution, and deployment outcome as separate facts.
- Add configured delivery completion rules and linked failure issues.
- Add product-monitor adapters only for explicitly configured signals.
- Deduplicate monitor findings and prevent repeated duplicate issues.

### 7. Migration and documentation

- Stop old writers before migrating existing JSON checkpoints to SQLite.
- Preserve IDs, pending batches, branches, handled feedback, reply outbox, retry
  budgets, and unresolved/uncertain publication states.
- Back up original JSON; validate imported counts and references before cutover.
- Run either old or new orchestration for a work item, never both.
- Keep compatibility entry points temporarily; retire duplicate orchestration
  after parity is verified.
- Document commands, diagrams, contracts, recovery, configuration, and extension seams.
- Explain that rollback requires quiescing new workers and reconciling new effects;
  a stale JSON backup cannot safely resume after new runtime publication.

## Observability

Use existing WorkflowBuilder and OTLP support for high-level execution phases.
Record work-item, message, causation, execution, spec revision, and commit identity.
Waiting periods, queue polls, and source scans produce local logs rather than
long-running agent/phase spans. Resume execution in a new trace linked through
correlation metadata. Expose current state, pending decision, last failure, next
retry, and evidence references for inspection.

## Validation and acceptance criteria

Use fake agents/GitHub, temporary SQLite stores, and local Git remotes. Test the
behavioral risks directly; live agents or external GitHub writes are not required.

- User request and GitHub spec both reach the shared developer.
- Address/CI repair reuses the same work item and PR branch.
- All triage outcomes and human revision loops work.
- Duplicate deliveries schedule one logical action; separate consumers retain
  independent delivery state.
- Crash after claim, after state commit, after push, and after reply reconciles
  without losing work or blindly repeating effects.
- Lease expiry cannot create two concurrent writers.
- New commits/spec edits invalidate stale approvals and evidence.
- Out-of-order CI/review events do not regress current readiness.
- Ordinary comments and unauthorized commands do not trigger execution.
- Paused/parked/blocked work resumes correctly; cancellation preserves recovery data.
- Verification failure and push rejection prevent premature success replies.
- PR closure/reopening and late review feedback are reconciled.
- Delivery failure creates one linked actionable issue; monitoring continues.
- Migration preserves pending publication and reply recovery state.
- Run focused workflow/infrastructure tests, existing dev/babysitter regressions,
  and the repository's complete test suite before cutover.

## Remaining configuration decisions

Implementation defaults are single-host SQLite, bounded workers, existing GitHub
polling, user-controlled merge, and no production monitoring until configured.
Before enabling deployment-specific paths, configure authorized actors, eligible
repositories/issues, verification commands, retry budgets, deployment signals,
and issue completion criteria.

An external broker, hosted webhooks, distributed workers, and automatic merge
policy are later extensions. They do not block the first unified vertical slice.

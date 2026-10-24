# Unified event-driven SDLC workflow

Status: Proposed implementation plan
Date: 2026-10-24
Repository: antshc/loop

## Goal

Build one durable workflow driven by messages from users, GitHub issues, pull
requests, agents, CI, and delivery monitoring. One workflow instance follows each
work item through triage, specification, implementation, review, verification,
human approval, delivery, and follow-up.

The developer accepts eligible, dependency-ready implementation tickets or
justified fix requests from Address or Maintainer. A user can submit a direct request,
including a complete implementation plan; the workflow transforms it into a
GitHub issue labeled `spec` before ticketing or repository work. A `spec` issue
is a planning parent, not an implementation task. Existing dev and PR babysitting
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
11. `spec` is the single GitHub planning-entry label; do not create, require,
    or route a `plan` label. Direct requests, even when they supply an
    implementation plan, are published as `spec` issues.
12. Triage automatically discovers native sub-issues on each eligible `spec`.
    If sub-issues exist, adopt their graph without regenerating it. Otherwise,
    create tickets automatically without user approval or default `hitl` labels,
    then immediately schedule the unblocked execution frontier.
13. `hitl` is an explicit hold for tickets needing human intervention, not a
    default label or a representation of native ticket dependencies. A held issue
    is skipped until `hitl` is removed and readiness is rechecked.

## Decision flow with user triggers

```text
USER
  +-- Direct request (may include implementation plan)
  |     +-- TRIAGE -> Clarify or park when needed
  |     +-- TO-SPEC -> Publish GitHub issue labeled spec
  |
  +-- GitHub issue opened / updated / released
        +-- Current label hitl -> SKIP; wait for removal -> Re-evaluate
        +-- spec issue -> TRIAGE: discover native sub-issues
        |     +-- Existing sub-issues -> Reuse tickets and blocker graph
        |     +-- No sub-issues -> TO-TICKETS creates children + dependencies
        |     +-- Schedule all ready tickets without waiting for approval
        +-- Child issue -> Check labels + dependencies -> Developer if ready
        +-- Human-blocked ticket -> Optional hitl hold -> Await user
        +-- Other issue -> Ignore unless explicitly enrolled

READY, NON-HITL CHILD TICKET
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
| Human | Resolve intervention blockers, remove `hitl` holds, review code/evidence, authorize merge or auto-merge |

Address and Maintainer MUST delegate code fixes through the shared developer task
contract. Agents MUST NOT bypass workflow policy by directly invoking another
agent or publishing changes. A successful agent process is not proof that the task
or verification succeeded.

## Bundled spec and ticketing instructions (implementation task)

During implementation, copy the instructions and all required references from:

- [Brain `to-spec` skill](https://github.com/antshc/brain/tree/main/plugins/wf/skills/to-spec)
- [Brain `to-tickets` skill](https://github.com/antshc/brain/tree/main/plugins/wf/skills/to-tickets)

This is **planned work**: no skill instructions are bundled in Loop yet.
The running factory MUST use repository-local copies, never fetch Brain at runtime.

The implementation MUST:

1. Copy the `SKILL.md` instructions and all directly required references:
   `to-spec/github.md`, `to-tickets/references/github.md`, and
   `to-tickets/references/issue-template.md`. Include any other substantive
   instructions needed to make the copied guidance self-contained.
2. Put these copies under `workflows/factory/agents/references/`.
   Record the original GitHub URLs and source commit/SHAs for traceability.
3. Add `agents/spec.md` and `agents/ticketing.md` as the actual agent entry
   prompts. Each MUST reference its local instructions and templates. The agent
   profile/builder MUST resolve and include those local files in the prompt.
4. Adapt copied operational rules to this factory's decisions: direct requests
   (including plans) become `spec` issues; triage reuses pre-existing sub-issues;
   a missing ticket graph is generated automatically without user approval or
   default `hitl`; native dependencies determine ready work; explicit
   human-blocked tickets may use `hitl`.
5. Keep GitHub discovery, duplicate checks, labels, child issue creation,
   dependency links, publication, retries, and confirmation in Python handlers.
   Agents only synthesize structured spec/ticket drafts; copied `gh` commands
   are adapter references, not instructions for agents to publish directly.
6. Remove or replace copied calls to other external skills (`doc-contracts`,
   `init-wf`, etc.) with bundled instructions or explicit Python contracts.
   No runtime dependency on the Brain repository or its skill loader remains.
7. Keep the local copies as the source of truth for execution. Any upstream
   changes require an explicit sync/review; do not silently overwrite locally
   adapted policy on a new Brain commit.

Current upstream `to-tickets` requires breakdown approval and `hitl` on all
created tickets. These two rules MUST be adapted during copying; they MUST NOT
override the automatic execution policy in this plan. Python validates agent
results before publishing. The reference copies are guidance, not executable
GitHub action scripts.

## GitHub and direct-user triggers

Commands below are proposed exact comment commands. Accept them from configured
authorized actors only. Use GitHub review actions for review approval/changes.
Direct-user requests enter the GitHub Issues path by first publishing/linking
a `spec` issue.

### GitHub issues

| User action | Message | Workflow action |
|---|---|---|
| Submit direct user request, with or without a plan | TaskSubmitted | Persist request and plan context; triage missing scope; run `to-spec` to create/link a GitHub `spec` issue before ticketing or code changes |
| Open/relabel/reopen a `spec` issue without `hitl` | SpecTicketingRequested | Reuse existing sub-issues or auto-publish missing ticket graph; schedule its ready non-`hitl` frontier immediately |
| Create or update a non-`hitl` child ticket | TicketReadyCheck | Re-read dependencies and scope; schedule automatically if ready |
| Remove `hitl` from an issue | IssueReleased | Re-read current labels, parent links, dependencies, and readiness; enqueue eligible work |
| Open a spec child ticket with `hitl` | None | Skip; do not schedule agents or other issue processing |
| Open/relabel/reopen any other `hitl` issue | None | Skip irrespective of `spec`, command, or other labels |
| Open unclassified GitHub issue | None | Ignore unless explicitly enrolled by configuration |
| Comment /implement on issue | ImplementationRequested | Check scope/readiness; implement or request missing decisions |
| Comment /spec on issue | SpecificationRequested | Create/revise specification |
| Comment /approve-spec on issue | SpecApproved | Optionally record approval of an exact spec revision; ticket discovery/breakdown does not wait for this |
| Reply to a pending issue question | ClarificationProvided | Resume the waiting decision |
| Request changes on pending spec | SpecChangesRequested | Return to Spec with feedback |
| Comment /retry on issue | RetryRequested | Reconcile existing effects and retry eligible blocked/failed work |
| Comment /pause on issue | PauseRequested | Stop scheduling new work; let active work reach a safe checkpoint |
| Comment /resume on issue | ResumeRequested | Reconcile current state; resume paused/parked work |
| Close issue | WorkCancelled | Stop new work; preserve recovery data |
| Reopen issue | WorkReopened | Reconcile and re-triage or resume the appropriate state |

### Pull requests

| User action | Message | Workflow action |
|---|---|---|
| Submit PR review requesting changes | ReviewReceived | Schedule Address |
| New eligible review comment/follow-up | ReviewReceived | Schedule Address; deduplicate by comment revision |
| Approve PR | PullRequestApproved | Record approval for reviewed commit; re-evaluate gates |
| Merge PR | PullRequestMerged | Track CI/CD and delivery |
| Explicitly enable auto-merge | AutoMergeEnabled | Let GitHub enforce required checks and approvals |
| Comment /retry on PR | RetryRequested | Reconcile existing effects and retry eligible blocked/failed work |
| Comment /pause on PR | PauseRequested | Stop scheduling new work; let active work reach a safe checkpoint |
| Comment /resume on PR | ResumeRequested | Reconcile current state; resume paused/parked work |
| Close unmerged PR | WorkCancelled | Stop new work; preserve recovery data |
| Reopen PR | WorkReopened | Reconcile and resume the appropriate state |

For GitHub issue events, evaluate `hitl` first, before any command, agent, or
transition. Skip every issue currently labeled `hitl`: no triage, specification,
ticket breakdown, implementation, comment, or issue closure. Label removal is a
new event, not retroactive approval; re-read labels and native dependencies before
scheduling. A still-open or unresolved `hitl` blocker prevents dependant tickets
from becoming ready. Do not use `plan` as a routing label; a plan is input
content, not a separate issue type.

`spec` issues are planning parents. Whenever triage receives an eligible `spec`,
read its native GitHub sub-issues and blockers first (including closed children):

- Existing sub-issues: adopt them as the authoritative ticket graph, retaining
  their issue IDs, labels, status, dependencies, and user edits. Do not regenerate,
  replace, relabel, or create additional children solely from a fresh breakdown.
  Report coverage gaps or invalid dependencies for separate resolution.
- No sub-issues: automatically use the `to-tickets` breakdown rules to derive
  tracer-bullet child tickets, dependencies, and waves. Publish/link them without
  requesting user approval. Do not apply `hitl` by default. Once the graph is
  confirmed, immediately enqueue its unblocked non-`hitl` frontier for the
  developer; advance dependent tickets as their blockers complete.
- Interrupted workflow-owned publication: reconcile persisted intended tickets
  against children already created, finish only missing intended tickets/links,
  and prevent duplicates. This recovery exception does not rewrite a pre-existing
  user-managed ticket graph.

Apply `hitl` only when human intervention is needed (for example a missing
decision, approval, or access). Record the reason and request the needed action
before placing the hold. Such a ticket is skipped until the user removes the
label; recheck dependencies then. A native blocked-by edge alone does not
require `hitl`: wait for its blocker to complete and recalculate the frontier.
Do not implement a spec parent or silently remove an existing `hitl`.
An implementation plan can be attached to a spec or supplied through a direct
request, but it does not create a separate issue type or bypass these gates.

Use the `to-spec` contract for every direct request, including one with a
ready-made plan: synthesize its requirements and decisions into a GitHub
`spec` issue with source/base routing labels, then follow the spec-to-tickets
route. Preserve substantive plan decisions; request missing initiative/base
metadata when required by the publication contract. Do not use a `plan` label.
Reconcile duplicate submissions before publishing another spec. Keep the tracker
issue repository distinct from the source-code repository.
`to-spec` and `to-tickets` are authoring contracts to adapt into Python
handlers. Preserve publication and duplicate-prevention guards. The current
`to-tickets` skill requires both explicit breakdown approval and `hitl` on
every new ticket; both rules MUST change (or an automatic workflow-specific
handler must supply equivalent breakdown behavior) before reuse. This plan
retains explicit human holds and PR approval, but neither approval nor manual
release is a default prerequisite to ticket implementation. Sources:
[to-spec](https://github.com/antshc/brain/tree/main/plugins/wf/skills/to-spec)
and [to-tickets](https://github.com/antshc/brain/tree/main/plugins/wf/skills/to-tickets).

Ordinary issue comments provide context. Only explicit commands and replies
correlated with a pending question trigger issue workflow actions. PR review
comments and review submissions are explicit review inputs. Record question IDs,
actor identity, source object ID/revision, and the decision being answered.

A `spec` label triggers ticket discovery/breakdown without requiring spec or
breakdown approval. A later spec edit MUST NOT silently replace existing child
tickets; report drift and require a separate explicit change request. Where a
separate human decision is genuinely needed, correlate it with an exact
revision/hash; do not interpret ordinary comments as approval.

Configure enrolled repositories and eligible issue/PR sources. Ingest issue
opened, labeled, unlabeled, and reopened events (or their polling equivalents).
Re-read current labels rather than trusting a stale webhook payload; deduplicate
label changes and polling against the same source revision. On first scan,
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
| TaskSubmitted / WorkReopened | Apply label guard; for direct requests publish/link a spec issue; otherwise reconcile the eligible workflow |
| SpecTicketingRequested | Apply label guard; inspect native sub-issues; adopt existing graph or trigger automatic breakdown |
| TicketGraphMissing | Persist breakdown intent; publish/reconcile child tickets and native blockers without approval or default `hitl` |
| TicketGraphDiscovered | Persist existing child IDs/dependencies; immediately schedule the non-`hitl`, dependency-ready frontier |
| TicketCompleted | After verified integration, re-evaluate native dependants and schedule newly ready non-`hitl` tickets |
| HumanInterventionRequired | Record reason; optionally label the blocked ticket `hitl` and stop scheduling it |
| IssueReleased | Recheck current labels, parent/dependencies, and readiness; schedule only eligible work |
| ClarificationProvided | Resume recorded pending decision, subject to current `hitl` guard |
| SpecApproved | Record optional revision approval; never gate automatic ticket discovery/breakdown |
| FixRequested | Schedule developer for current branch/revision |
| ImplementationCompleted | Validate/publish candidate; schedule review and verification |
| ReviewReceived | Address |
| ChecksFailed | Maintainer |
| VerificationCompleted | Evaluate evidence and human-review readiness |
| PullRequestApproved | Re-evaluate merge readiness |
| PullRequestMerged | Track delivery |
| DeploymentFailed | Diagnose/report linked issue |
| IssueDetected | Create linked issue/work item; triage |
| WorkBlocked | Persist reason; for dependency blocks wait for predecessor, for human blocks request input and optionally add `hitl` |

## State, gates, and concurrency

Suggested lifecycle states: received, triaging, awaiting_clarification, parked,
specifying, ticketing, implementing, reviewing, verifying,
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
- Check `hitl` before processing any issue-derived event, including `/implement`
  and any previously queued action. Label removal triggers re-evaluation; do not
  automatically remove or bypass `hitl`.
- Triage checks for existing native sub-issues before breakdown; existing graphs
  are reused, and only a genuinely missing graph is generated automatically.
- Workflow-created children are not held by default. Schedule ready tickets
  immediately after breakdown and as blockers complete; no breakdown approval
  or ticket-release action is needed in the normal path.
- Add `hitl` only for explicit human intervention; a native dependency blocker
  merely delays scheduling, without requiring a label.
- A direct request (including one with a plan) MUST first publish/link a `spec`
  issue; only non-`hitl`, dependency-ready children enter implementation.
  `/implement` cannot bypass the spec/ticket path or label guard.
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
      ticketing.md
      references/
        to-spec/
          SKILL.md
          github.md
        to-tickets/
          SKILL.md
          references/
            github.md
            issue-template.md
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

The tree above is the target implementation layout; its factory assets are not
created by this plan update. Profiles and complete instructions stay together
in agents/. Keep state outside tracked files, for example
.loop/factory/state.sqlite3, logs, evidence, and worktree roots. Do not
duplicate prompts or Git/worktree lifecycle implementations.

## Implementation phases

### 1. Contracts and decision flow

- Copy and adapt both Brain authoring skills and required references into the
  planned local agent-reference tree; record upstream source SHAs.
- Wire `spec.md` and `ticketing.md` entry prompts through the agent builder
  so local instructions/templates are included at execution time.
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

- Route direct requests, including ones with implementation plans, through
  `to-spec` and `to-tickets`; automatically send all non-`hitl`,
  dependency-ready children to the developer.
- Adapt existing dev task execution behind the shared developer handler.
- Align commit responsibilities with Python-owned verification/publication.
- Accept eligible spec-ticket references and justified fix requests through
  the same developer task contract.
- Preserve dry/fake seams and reuse existing profiles/execution APIs.
- First vertical slice: direct request/plan -> GitHub `spec` issue -> reuse
  existing sub-issues or auto-create children -> schedule ready tickets ->
  developer -> verified draft PR.

### 4. PR review and CI

- Turn existing babysitter polling into message ingress and finite handlers.
- Reuse feedback grouping, fingerprints, publication checkpoints, and reply recovery.
- Refactor Address/Maintainer to request developer fixes.
- Review and CI messages resume the same work item/branch.
- Preserve late-feedback handling, author/fork checks, per-SHA retry behavior,
  and reviewer-owned thread resolution.

### 5. Specification and human gates

- Use the locally bundled `to-spec` guidance for all direct requests, with or
  without supplied plans. Do not fetch Brain at runtime.
- Triage native sub-issues and reuse pre-existing ticket graphs unchanged.
- Use locally bundled and adapted `to-tickets` guidance for unattended
  breakdown/publication when a spec has no children. Remove the upstream
  approval guard and mandatory-`hitl` label guard from the executable prompt;
  maintain idempotent creation, native blockers, and crash recovery.
- Publish no `plan` label; a supplied plan becomes content of a `spec` issue.
- Create new children without `hitl` by default. Queue the ready frontier
  immediately; label `hitl` only for explicit human intervention blockers.
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

- A direct request, including one with a complete plan, creates/links one
  GitHub issue labeled `spec` with routing metadata before repository work.
- The source plan's substantive decisions survive conversion to the spec;
  no `plan` label is created or used as a trigger.
- The factory loads its spec/ticketing agent instructions and templates entirely
  from the local workflow package; no runtime Brain calls or missing references.
- The copied source is adapted so agents never perform GitHub writes, solicit
  ticket breakdown approval, or add `hitl` to ordinary generated tickets.
- Triage automatically adopts existing native `spec` sub-issues without
  changing them; if none exist, it creates a complete ticket graph without user
  approval or default `hitl` labels.
- Interrupted publication reconciles intended tickets/links without duplicates;
  pre-existing user-authored child graphs are not expanded automatically.
- After graph discovery/publication, non-`hitl` tickets with no unresolved
  blockers are scheduled without human intervention; completing a blocker
  advances its dependants automatically.
- With A blocking B, A starts automatically and B waits without `hitl`;
  once A is verified and integrated, B starts automatically.
- Tickets needing human decisions/access may receive `hitl` with recorded
  reason and intervention request; removing it resumes only after readiness
  checks. Existing `hitl` labels remain authoritative.
- Only eligible, dependency-ready child tickets reach the developer; the spec
  itself is never directly implemented.
- Any issue with `hitl` is skipped, even on `/implement`, reopen, relabel,
  or queued retry; removing it rechecks blockers/readiness without bypassing gates.
- Unclassified issues do not start workflows; direct requests do not bypass
  spec publication, and no separate `plan` issue route exists.
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
repositories/issues, label precedence and release permissions, verification
commands, retry budgets, deployment signals, and issue completion criteria.

An external broker, hosted webhooks, distributed workers, and automatic merge
policy are later extensions. They do not block the first unified vertical slice.

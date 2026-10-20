# Plan: Append-Only Agent Execution Receipts and Regression Evaluations

**Status:** Proposed — architecture and implementation plan; not implemented  
**Scope:** User-owned Loop Workflows (initial integration: workflows/dev/) and target application repositories. The public src/loop/ API is unchanged in the MVP.  
**Principle:** Today's receipt becomes a candidate for tomorrow's eval. Preserve failures, false passes, retries, and escalations—not just successful outcomes.  
**Related:** [Proof for a .NET REST Backend with Real Dependencies](proof-net-backend-real-dependencies.md), [Feature Maps for Agent-Driven Verification](feature-map-agent-verification.md), [Scenario-Driven BDD and Allure Architecture](scenario-driven-bdd-allure-architecture.md)

## 1. Problem

An autonomous coding workflow can complete many tasks without generating a reliable historical dataset. A successful final commit can conceal failed attempts, missing checks, environment failures, or a misleading PASS. Without preserved inputs, actions, verification evidence, and outcomes, subsequent agent or prompt changes cannot be compared reproducibly against historical behavior.

**A receipt records what happened; an evaluation specifies what should happen and how to judge it.** Recording a PASS is not evidence that all requirements were checked. Evaluation cases are deliberately curated from receipts and reviewed requirements; they are not blindly generated from the agent's own verdict.

## 2. Goals and non-goals

### Goals

- Persist a complete, immutable receipt for **every attempt**, including retries, crashes, timeouts, skipped checks, and human escalations.
- Correlate the request, agent execution, source revision, commands, proof results, code changes, and final workflow decision.
- Retain failure evidence even if the agent later fixes the problem or the worktree is restored/deleted.
- Reuse existing .NET unit, integration, API, database, messaging, and optional browser tests through the separate Proof mechanism.
- Promote selected receipts into version-controlled, reproducible regression evaluations with independently defined expectations.
- Compare agent profiles, models, prompts, tools, or orchestration versions using consistent inputs and verification criteria.
- Report evidence gaps and false PASS discoveries explicitly.

### Non-goals

- Replacing Proof, xUnit/NUnit/MSTest, Testcontainers, Playwright, or existing application tests.
- Assuming a replay will be byte-for-byte deterministic, or storing an agent's original output as the expected answer.
- Treating a historical PASS, green build, or zero failing tests as sufficient proof of correct behavior.
- Implementing a platform-wide evaluation service, database, or new public Loop API for the MVP.
- Committing secrets, personal data, unredacted prompts, or large binary artifacts to Git.
- Running replay tests against production or destructively modifying shared environments.

## 3. Separation of responsibilities

| Component | Responsibility |
|---|---|
| **Agent** | Produces code, tool activity, and an execution report. Cannot certify its own acceptance. |
| **Loop Workflow** | Owns attempt identity, orchestration, retry/escalation decisions, receipt finalization, and references to artifacts. |
| **Proof** | Independently executes approved checks and emits structured PASS / FAIL / UNKNOWN plus evidence. See the linked Proof plan. |
| **Receipt writer** | Appends an immutable attempt record and persists/redacts its artifacts, including during failure and cleanup. |
| **Eval curator** | Promotes interesting receipts into tasks with reviewed expectations, fixtures, and stable required checks. May involve a human. |
| **Eval runner** | Runs an agent candidate in a clean environment and invokes the *same independent evaluation policy* across candidates. |
| **Evaluator** | Computes per-case verdict from expected behavior, mandatory Proof results, evidence completeness, and policy; does not trust the agent's self-report. |

The workflow is the application and may depend on the public Loop library. The public Loop library must not depend on these workflow-side components or on .NET testing infrastructure.

## 4. Receipt lifecycle

~~~text
Task / Ticket + revision + agent configuration
                      |
                      v
               Begin attempt
                      |
                      v
        Agent actions and code changes
                      |
                      v
       Independent verification (Proof)
                      |
                      v
          Finalize receipt + artifacts
            /          |           \
         PASS         FAIL        UNKNOWN
           \            |           /
            \       retry /       /
             \    escalation     /
              v                 v
          Append every attempt; never overwrite
                      |
                      v
          Select interesting candidate cases
                      |
                      v
          Review expected behavior and checks
                      |
                      v
           Versioned evaluation dataset
                      |
                      v
        Replay against new agent configuration
                      |
                      v
        Compare evidence-backed outcomes
~~~

**Must-hold ordering:** Capture evidence before resetting the worktree, closing a ticket, removing the worktree, or cleaning test environments. Finalization must also happen if the agent process raises an exception. A receipt-writing error must be surfaced; it must not silently turn a failed or unverified attempt into success.

## 5. Receipt contract

Every attempt has a unique **attempt ID**, stable parent **workflow run ID**, and optional **spec/ticket ID**. The attempt is a distinct unit from the eventual task result. For example, a task might have three receipts: two failures and one success.

Proposed JSON envelope (illustrative, not a currently implemented schema):

~~~json
{
  "schemaVersion": "1.0",
  "receiptId": "attempt-20261018-002",
  "workflowRunId": "dev-20261018-001",
  "attemptNumber": 2,
  "task": {
    "id": "ticket-101",
    "source": "https://github.com/example/harness/issues/101",
    "goal": "Reject invalid order input with HTTP 400",
    "requirementsRef": "requirements/order-validation.md"
  },
  "source": {
    "repository": "example/orders-api",
    "baseRevision": "a1b2c3d",
    "resultRevision": "d4e5f6a",
    "worktree": "ephemeral"
  },
  "agent": {
    "profile": "codey",
    "model": "recorded-model-identifier",
    "promptRef": "artifacts/prompt.redacted.txt",
    "configurationDigest": "sha256:..."
  },
  "timeline": {
    "startedAt": "2026-10-18T20:00:00Z",
    "finishedAt": "2026-10-18T20:04:00Z"
  },
  "artifacts": {
    "agentTrace": "artifacts/agent-trace.redacted.jsonl",
    "patch": "artifacts/changes.patch",
    "commands": "artifacts/commands.jsonl",
    "proof": "artifacts/proof.json",
    "testResults": ["artifacts/orders.trx"]
  },
  "verification": {
    "verdict": "FAIL",
    "requiredChecks": 5,
    "passedChecks": 4,
    "failedChecks": 1,
    "missingChecks": 0
  },
  "outcome": {
    "agentReportedStatus": "completed",
    "workflowStatus": "retry",
    "reason": "Expected HTTP 400; received HTTP 500",
    "escalated": false
  }
}
~~~

**Required fields:** receipt/schema identity, task identity, source revision, agent/configuration reference, timing, independent verification status (including NOT_RUN where appropriate), artifact manifest, workflow result, and error/finalization state. Include observed actions, command exit codes, stdout/stderr references, files changed, test selection/discovery counts, and correlation IDs when available. Missing data must be explicit, never invented.

**Status semantics:**

- **PASS:** All required checks ran, passed, and produced sufficient evidence under the approved policy.
- **FAIL:** A required check or validated workflow invariant failed.
- **UNKNOWN:** Infrastructure, configuration, missing tests, incomplete evidence, or interruption prevents a reliable determination.
- **NOT_RUN:** Verification was not attempted; record a reason. This is not PASS.
- Agent-reported and workflow statuses are separate from the independent Proof verdict (e.g., agent says "completed" while Proof says FAIL).

A receipt can additionally record a **coverage gap**, including a false PASS discovered later. A correction/annotation references the original receipt; never rewrite history.

## 6. Storage and append-only behavior

Proposed local layout in an executing harness (not repository-tracked by default):

~~~text
.loop/
  runs/
    dev-20261018-001/
      attempts/
        attempt-001/
          receipt.json
          artifacts/
            agent-trace.redacted.jsonl
            commands.jsonl
            changes.patch
            proof.json
            orders.trx
        attempt-002/
          receipt.json
          artifacts/
            ...
      index.jsonl
~~~

Each receipt is **written once**. Use temporary-file creation plus atomic rename where supported; maintain a manifest with artifact paths and SHA-256 checksums. Append an index entry only after the attempt's receipt and artifacts are persisted. On process crash, recovery must preserve a distinct incomplete attempt with UNKNOWN and its surviving artifacts; never repurpose an existing ID.

For cross-machine/CI evaluation, upload artifacts to an approved retention store and store stable artifact references. Git should contain curated **eval definitions**, not every raw trace or dependency snapshot.

**Existing implementation note:** workflows/dev/journal.py currently persists mutable daily per-Ticket failure counters via FileExecutionStore, and resets counters after delivery. Keep these retry semantics for now: the append-only receipt store is a **separate concern**, not a replacement for failure counting. In workflows/dev/attempt.py, evidence must be saved *before* git.restore on failure and before receipt-losing cleanup on success.

**Retention/security:** Redact tokens, credentials, personal data, request bodies, database values, and agent-visible secrets at collection time. Apply access controls and a configurable retention policy. Hash or reference large test artifacts; set per-artifact size limits and explicitly note truncation. Protect manifests and eval definitions from untrusted artifact-supplied instructions.

## 7. From receipt to evaluation

**Promote only a selected, reviewed case.** Candidate triggers:

- A previously failed task that was fixed.
- A discovered false PASS (missing assertion; runtime error despite green reported result).
- A regression after changing model, prompt, tools, or workflow policy.
- A representative difficult or high-value task with clear acceptance criteria.
- An escalation or environment failure worth testing for correct UNKNOWN/escalation routing.

Promotion procedure:

1. Select a receipt and linked evidence, including the original failing attempt.
2. Pin the **starting repository revision** and the original task/requirements (not the final fixed commit).
3. Define expected **externally observable behavior** from reviewed requirements, not from the agent's proposed patch.
4. Identify mandatory versioned Proof manifests and existing tests; add a missing regression assertion if the original receipt exposed a false PASS.
5. Record environment/dependency requirements and reset/seed strategy.
6. Review security, test determinism, isolation, and eligibility for automated replay.
7. Commit an eval definition and a link to the source receipt. Do not embed sensitive raw artifacts.

Proposed evaluation layout (in the harness or application repository as appropriate):

~~~text
evals/
  orders-invalid-input/
    case.yaml
    README.md
    fixtures/               # Sanitized, versioned and optional
~~~

Proposed case example (illustrative):

~~~yaml
id: orders-invalid-input
originReceipt: attempt-20261018-002
task:
  goal: Reject malformed POST /orders requests with HTTP 400
  requirements: requirements/order-validation.md
repository:
  slug: example/orders-api
  startRevision: a1b2c3d
environment:
  mode: black-box
  dependencies: [postgres, rabbitmq]
  reset: disposable-instance
verification:
  proofManifest: proof/orders-invalid-input.yaml
  requiredChecks:
    - invalid-payload-returns-400
    - no-order-persisted
expected:
  verdict: PASS
comparison:
  ignore:
    - timestamps
    - generated-identifiers
~~~

This case captures the **task and acceptance oracle**, not a required source-code diff. Verify that fixtures, tests, and proof policies are version-pinned or otherwise reproducibly resolved. An eval cannot legitimately pass if required checks were not discovered/executed.

## 8. Replaying evaluations

~~~text
Choose eval suite + candidate agent version
                |
                v
Create isolated checkout at pinned startRevision
                |
                v
Provision allowed, disposable dependency environment
                |
                v
Run candidate agent on original task
                |
                v
Run approved Proof manifest and collect evidence
                |
                v
Write new receipt + eval result; clean up environment
                |
                v
Compare with other candidates on the same cases
~~~

**Invariants:**

- The agent must not be shown the expected implementation, hidden assertions, prior successful diff, or original failure's repair.
- Required checks and evaluation policy are frozen per comparison; the coding agent cannot weaken or omit them.
- Data, clocks, dependency versions, and network exposure are controlled or their variability is recorded.
- Distinguish application failure from infrastructure failure: classify uncertain runs UNKNOWN and rerun only according to a declared flakiness policy.
- Prefer repeated trials for stochastic agents. Report trial count and confidence/variability, not an unsupported universal success claim.
- Isolate parallel trials, especially databases, message queues, cloud resources, credentials, and worktrees.
- Record agent version, prompts, tooling, model, token/cost metadata when available, elapsed time, retries, and escalations.

Suggested scorecard per candidate: task success rate, first-attempt success rate, false-PASS rate, regression rate, UNKNOWN rate, escalation correctness, elapsed time and cost. Always show denominators, excluded/unknown cases, and eval suite revision; do not cherry-pick passes.

### Concrete .NET REST API example

A task requires invalid POST /orders payloads to return HTTP 400 without persisting an order.

- **Receipt 1 — FAIL:** Agent introduces a fix, but a .NET system test gets HTTP 500. Store patch, TRX, correlated HTTP exchange, server logs, and Proof FAIL.
- **Receipt 2 — PASS:** Agent retries and all approved checks pass. Keep receipt 1 intact; do not retain only the successful final state.
- **Receipt 3 — apparent PASS / coverage gap:** A later run reports PASS even though the API fails for another invalid payload. The original checked cases were insufficient. Append a review annotation and add a regression test.
- **Eval:** Recreate the original task on its starting revision in an isolated environment with real PostgreSQL and the actual ASP.NET Core API. Execute the updated approved system test suite through Proof. Compare candidate agent versions based on independent behavior, not matching patches.

Proof may run existing dotnet test suites, API scenarios, database probes, broker checks, and optional Playwright flows; it owns the verdict and evidence contract. See the linked Proof plan for dependency fidelity and PASS/FAIL/UNKNOWN rules.

## 9. Integration with current Loop workflows

The first implementation should live in **workflows/dev/** as workflow-owned code:

1. Introduce a small AttemptReceipt / ReceiptStore contract (or equivalent functions) and file implementation; avoid a generic framework.
2. In **attempt.py**, allocate a new attempt ID before calling run_agents. Record task, source SHA, configuration, start time, and artifact directory.
3. Capture trace/command/proof artifact references during agent execution where supported. Avoid logging secrets or duplicating large streams.
4. In a finally-safe boundary, snapshot changes and verification evidence **before** existing rollback, success reset, or worktree cleanup. Finalize even for exceptions and aborted attempts.
5. Preserve existing _result_violation, _commit_violation, retries, ticket updates, and FileExecutionStore behavior. A valid commit is not equivalent to verified product behavior.
6. Add optional independent Proof invocation at the workflow-level acceptance boundary as proposed in the linked Proof plan. Until integrated, mark the Proof verdict NOT_RUN rather than pretending the current commit checks are full product verification.
7. Build separate manual eval curation and replay commands/scripts in the harness; a replay is not part of the normal ticket-delivery critical path.
8. Keep console steps/events lightweight and link to the receipt ID; integrate optional reporting with the linked scenario-driven logging plan without making Allure a required runtime dependency.

### Milestones

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **1. Record** | Attempt IDs, receipt schema/store, partial/crash capture, redaction, artifact manifest | Successful, failed, retried and escalated attempts all leave separate durable receipts; rollback cannot erase evidence |
| **2. Verify** | Receipt references to approved Proof results and required check counts | Missing/zero executed checks cannot become PASS; FAIL and UNKNOWN remain distinct |
| **3. Curate** | Reviewed YAML eval cases and controlled fixtures | A selected historical incident has reproducible task input, starting revision, and independent oracle |
| **4. Replay** | Isolated evaluation runner and comparison output | Same case is run against two configurations with identical approved checks; evidence and outcomes are attributable |
| **5. Harden** | CI promotion policy, retention, flaky-trial handling, dashboards | Regressions are detectable without losing failures or leaking secrets |

## 10. Tests and acceptance criteria

- [ ] Every attempt gets a unique receipt, including subprocess failure, exception, timeout, and escalation.
- [ ] Retrying a Ticket appends another receipt; previous attempt files are unchanged.
- [ ] Restoring Git state or deleting a worktree does not delete already-captured patches, logs, or Proof artifacts.
- [ ] Crash recovery produces an incomplete/UNKNOWN record without falsifying the previous attempt.
- [ ] The writer validates schema, artifact presence/checksums, redaction, and atomic finalization.
- [ ] Proof NOT_RUN, missing required checks, and infrastructure failures never count as PASS.
- [ ] Existing FileExecutionStore failure-count behavior and ticket/PR flow remain unchanged.
- [ ] Curated evals reproduce the starting task, not its completed patch; behavioral checks are independent.
- [ ] A deliberately introduced false PASS becomes a coverage-gap annotation plus an executable regression check.
- [ ] Replays use isolated databases/brokers and clean checkouts, and do not touch production.
- [ ] Comparison reports identify suite revision, candidate configuration, trials, failures, unknowns, and artifact locations.
- [ ] Tests use fakes for store/orchestration boundaries; add an end-to-end sample against a disposable .NET backend.

## 11. Decisions still required before implementation

- **Artifact backend:** Local filesystem for MVP; decide when CI/object storage becomes mandatory and its retention period.
- **Proof integration:** Define the precise workflow acceptance gate and report path/contract after the separate Proof proposal is accepted.
- **Eval ownership:** Decide whether cases live with the harness, the target application, or both; keep approved test policies near code they validate.
- **Promotion policy:** Decide who reviews candidate evals and when automatic test generation is permitted.
- **Cost controls:** Set artifact size caps, maximum eval concurrency, dependency budgets, and stochastic trial count.

**Core rule:** Save every execution as evidence; promote only trustworthy, independently checkable cases to evaluations; use those evaluations to measure whether future agents improve.

# Agentic Software Factory: SDLC Workflow Catalog

**Status:** Research / proposed workflow model  
**Date:** 2026-10-27  
**Scope:** Event-driven software factory workflows across development, PR handling, deployment, production incidents, maintenance, and improvement.

**Related:** [Agentic Software Factory Lifecycle](agentic-software-factory-lifecycle.md) describes the eight industry-aligned lifecycle phases. This document decomposes the lifecycle into reusable, trigger-driven workflows.

## Findings

An agentic software factory coordinates specialized agents, deterministic gates, human decisions, and persistent work state. The taxonomy below is a **synthesis of vendor documentation and established SRE/DevOps practices**, not a formal industry standard. A workflow can start another workflow; stages may repeat.

| Operational stage | Purpose |
| --- | --- |
| Triage | Classify, deduplicate, route incoming work |
| Build | Specify, break down, implement |
| Validate | Review, test, secure, prove behavior |
| Release | Merge, deploy, measure rollout |
| Monitor | Detect incidents, mitigate, investigate |
| Improve | Maintain, document, learn, evolve factory |

Warp Factories documents foreman-led triage, spec, implementation, review, and human handoff. Factory's Software Factory extends this with release, documentation, and incident monitoring. GitHub provides PR review and merge controls. Google SRE supplies incident and production-safety practices.

## Workflow catalog

| # | Workflow | Trigger | Outcome |
| --- | --- | --- | --- |
| 1 | Work intake and triage | Issue, message, alert | Routed work item |
| 2 | Specification and ticket breakdown | Specification request or spec-labeled issue | Specification and linked tickets |
| 3 | Feature development | Ready implementation ticket | Tested implementation PR |
| 4 | Independent PR review | PR opened or synchronized | Review findings and recommendation |
| 5 | PR babysitting | Review comment, failed check, conflicting branch | Merge-ready PR or explicit blocker |
| 6 | PR merge coordination | All review and validation conditions satisfied | Merged PR and completion event |
| 7 | Release and deployment | Merged PR, version tag, schedule | Verified production release or rollback |
| 8 | Production incident response | Monitoring or paging alert | Mitigated incident and recorded follow-up |
| 9 | Production bug remediation | Customer report or incident follow-up | Permanent fix PR |
| 10 | CI failure recovery | Failed pipeline/check | Repaired build or recorded infrastructure blocker |
| 11 | Security vulnerability remediation | SAST, dependency or advisory alert | Validated security fix PR |
| 12 | Dependency maintenance | Schedule or package advisory | Compatible dependency update PR |
| 13 | Technical debt reduction | Schedule, quality rule, backlog | Behavior-preserving refactor PR |
| 14 | Documentation synchronization | Code, interface, or schema change | Updated documentation |
| 15 | Post-incident learning | Resolved incident | Reviewed postmortem and prevention tickets |
| 16 | Factory self-improvement | Repeated failures, cost or quality drift | Reviewed factory definition change |

## Text sequence diagrams

The Mermaid source is a text sequence diagram, renderable on GitHub. Participants describe responsibilities, not necessarily distinct processes. Decisions, loops, and human approvals are illustrative policy branches.

## Planning and development

### 1. Work intake and triage

**Trigger:** Issue, message, alert. **Outcome:** Routed work item.

```mermaid
sequenceDiagram
    actor User
    participant GitHub
    participant Triage
    participant Factory
    User->>GitHub: Create issue or request
    GitHub->>Triage: Issue event
    Triage->>GitHub: Check duplicates and context
    alt Ready
        Triage->>Factory: Route to workflow
    else Blocked
        Triage->>GitHub: Post questions and mark blocked
        GitHub-->>User: Await clarification
    end
```

**Gate:** Valid scope; duplicate check; known owner; sufficient context.

### 2. Specification and ticket breakdown

**Trigger:** Specification request or spec-labeled issue. **Outcome:** Specification and linked tickets.

```mermaid
sequenceDiagram
    actor User
    participant Spec
    participant GitHub
    participant Planner
    User->>GitHub: Create spec issue
    GitHub->>Spec: Analyze requirements
    Spec->>GitHub: Write specification
    opt HITL required
        GitHub-->>User: Request decision
        User->>GitHub: Approve or request changes
    end
    GitHub->>Planner: Break down specification
    Planner->>GitHub: Create or reuse linked tickets
```

**Gate:** Testable criteria; decisions resolved; HITL only when requested or blocked.

### 3. Feature development

**Trigger:** Ready implementation ticket. **Outcome:** Tested implementation PR.

```mermaid
sequenceDiagram
    participant GitHub
    participant Factory
    participant Developer
    participant Verifier
    participant CI
    GitHub->>Factory: Ready ticket
    Factory->>Developer: Assign isolated workspace
    Developer->>Developer: Implement and test
    Developer->>Verifier: Request behavioral proof
    Verifier-->>Developer: Evidence or failures
    loop Until verified or blocked
        Developer->>Developer: Repair findings
        Developer->>Verifier: Reverify
    end
    Developer->>GitHub: Open PR
    GitHub->>CI: Run required checks
```

**Gate:** Acceptance criteria; automated tests; independent verification evidence.

## Pull request lifecycle

### 4. Independent PR review

**Trigger:** PR opened or synchronized. **Outcome:** Review findings and recommendation.

```mermaid
sequenceDiagram
    participant GitHub
    participant Factory
    participant Reviewer
    participant CI
    GitHub->>Factory: PR opened or updated
    Factory->>Reviewer: Review current commit
    Reviewer->>CI: Read check results
    Reviewer->>Reviewer: Inspect code and evidence
    Reviewer->>GitHub: Publish review findings
    GitHub-->>Factory: Review complete
```

**Gate:** Correctness; requirements; security; tests; independent reviewer.

### 5. PR babysitting

**Trigger:** Review comment, failed check, conflicting branch. **Outcome:** Merge-ready PR or explicit blocker.

```mermaid
sequenceDiagram
    participant GitHub
    participant Maintainer
    participant Address
    participant CI
    loop Until ready or blocked
        GitHub->>Maintainer: PR status event
        Maintainer->>GitHub: Inspect unresolved blockers
        alt Fixable
            Maintainer->>Address: Request repair
            Address->>GitHub: Push fix and reply
            GitHub->>CI: Revalidate
        else Human decision required
            Maintainer->>GitHub: Post specific question
        end
    end
```

**Gate:** No blocking feedback; passing checks; no unresolved merge conflict.

### 6. PR merge coordination

**Trigger:** All review and validation conditions satisfied. **Outcome:** Merged PR and completion event.

```mermaid
sequenceDiagram
    participant Maintainer
    participant GitHub
    participant CI
    actor Human
    Maintainer->>GitHub: Check current eligibility
    GitHub->>CI: Validate merge candidate
    CI-->>GitHub: Required checks
    alt Protected rules satisfied
        opt Human approval required
            GitHub-->>Human: Request review
            Human->>GitHub: Approve
        end
        GitHub->>GitHub: Merge or enqueue
        GitHub-->>Maintainer: Merged event
    else Blocked
        GitHub-->>Maintainer: Report blockers
    end
```

**Gate:** GitHub-enforced protection rules and required approvals.

## Release and production

### 7. Release and deployment

**Trigger:** Merged PR, version tag, schedule. **Outcome:** Verified production release or rollback.

```mermaid
sequenceDiagram
    participant GitHub
    participant Release
    participant CD
    participant Monitor
    actor Human
    GitHub->>Release: Merge or release tag
    Release->>CD: Build immutable artifact
    opt Approval required
        Release->>Human: Request authorization
        Human-->>Release: Approve
    end
    Release->>CD: Deploy canary
    CD->>Monitor: Evaluate SLO and health
    alt Healthy
        Release->>CD: Complete rollout
    else Regression
        Release->>CD: Roll back
        Release->>GitHub: Create regression issue
    end
```

**Gate:** Release readiness; authorization; canary health; rollback available.

### 8. Production incident response

**Trigger:** Monitoring or paging alert. **Outcome:** Mitigated incident and recorded follow-up.

```mermaid
sequenceDiagram
    participant Monitor
    participant Incident
    actor OnCall
    participant Operations
    participant GitHub
    Monitor->>Incident: Production alert
    Incident->>Operations: Inspect logs and metrics
    Incident->>OnCall: Impact and mitigation proposal
    OnCall->>Operations: Authorize risky action
    Operations->>Operations: Roll back or mitigate
    Operations-->>Incident: Health evidence
    Incident->>GitHub: RCA and follow-up issue
```

**Gate:** Impact assessed; risky action authorized; service restoration observed.

### 9. Production bug remediation

**Trigger:** Customer report or incident follow-up. **Outcome:** Permanent fix PR.

```mermaid
sequenceDiagram
    participant Support
    participant Triage
    participant Developer
    participant GitHub
    participant CI
    Support->>Triage: Report and evidence
    Triage->>GitHub: Create or deduplicate issue
    Triage->>Developer: Request reproduction
    Developer->>Developer: Add regression test and fix
    Developer->>GitHub: Open linked PR
    GitHub->>CI: Validate
    CI-->>GitHub: Check results
```

**Gate:** Reproduction or clear causal evidence; regression coverage; passing validation.

## Recovery, security, and maintenance

### 10. CI failure recovery

**Trigger:** Failed pipeline/check. **Outcome:** Repaired build or recorded infrastructure blocker.

```mermaid
sequenceDiagram
    participant CI
    participant Triage
    participant Developer
    participant GitHub
    CI->>Triage: Failed run
    Triage->>CI: Read logs and artifacts
    alt Code regression
        Triage->>Developer: Assign repair
        Developer->>GitHub: Push fix
        GitHub->>CI: Rerun checks
    else Flaky test or infrastructure issue
        Triage->>GitHub: Record diagnosis and owner
    end
```

**Gate:** Classified failure; passing checks on relevant commit.

### 11. Security vulnerability remediation

**Trigger:** SAST, dependency or advisory alert. **Outcome:** Validated security fix PR.

```mermaid
sequenceDiagram
    participant Scanner
    participant Security
    participant Developer
    participant GitHub
    Scanner->>Security: Vulnerability alert
    Security->>Security: Validate impact and severity
    Security->>Developer: Request targeted fix
    Developer->>GitHub: Open remediation PR
    GitHub->>Security: Independent review
    Security->>GitHub: Findings and gate result
```

**Gate:** Threat impact confirmed; fix verified; sensitive disclosure protected.

### 12. Dependency maintenance

**Trigger:** Schedule or package advisory. **Outcome:** Compatible dependency update PR.

```mermaid
sequenceDiagram
    participant Scheduler
    participant Dependencies
    participant GitHub
    participant CI
    Scheduler->>Dependencies: Audit outdated versions
    Dependencies->>GitHub: Open update PR
    GitHub->>CI: Run compatibility tests
    alt Pass
        CI-->>GitHub: Ready for review
    else Failure
        CI-->>Dependencies: Report incompatibility
        Dependencies->>GitHub: Repair or flag blocker
    end
```

**Gate:** Tests; compatibility; licenses; supply-chain policy.

### 13. Technical debt reduction

**Trigger:** Schedule, quality rule, backlog. **Outcome:** Behavior-preserving refactor PR.

```mermaid
sequenceDiagram
    participant Scheduler
    participant Quality
    participant GitHub
    participant Developer
    Scheduler->>Quality: Assess quality indicators
    Quality->>GitHub: Create prioritized debt issue
    GitHub->>Developer: Assign bounded refactor
    Developer->>Developer: Preserve observed behavior
    Developer->>GitHub: PR with regression evidence
```

**Gate:** Behavior unchanged; improvement measured; test suite passes.

### 14. Documentation synchronization

**Trigger:** Code, interface, or schema change. **Outcome:** Updated documentation.

```mermaid
sequenceDiagram
    participant GitHub
    participant Docs
    participant CI
    actor Reviewer
    GitHub->>Docs: Code or API change
    Docs->>GitHub: Inspect affected documentation
    Docs->>GitHub: Propose update
    GitHub->>CI: Validate links and examples
    CI-->>Reviewer: Results and documentation PR
```

**Gate:** Examples run; links work; documentation agrees with code.

## Learning and improvement

### 15. Post-incident learning

**Trigger:** Resolved incident. **Outcome:** Reviewed postmortem and prevention tickets.

```mermaid
sequenceDiagram
    participant Incident
    participant RCA
    actor Human
    participant GitHub
    Incident->>RCA: Resolution event
    RCA->>RCA: Reconstruct timeline and contributing causes
    RCA->>Human: Submit postmortem
    Human->>RCA: Review and correct
    RCA->>GitHub: Create prevention issues
    GitHub->>GitHub: Enter normal SDLC
```

**Gate:** Credible cause analysis; owners and measurable actions.

### 16. Factory self-improvement

**Trigger:** Repeated failures, cost or quality drift. **Outcome:** Reviewed factory definition change.

```mermaid
sequenceDiagram
    participant Ledger
    participant Evaluator
    participant Improver
    participant GitHub
    actor Human
    Ledger->>Evaluator: Completed runs and outcomes
    Evaluator->>Evaluator: Identify repeated failure
    Evaluator->>Improver: Request candidate change
    Improver->>Improver: Benchmark candidate
    Improver->>GitHub: Submit factory-change PR
    GitHub->>Human: Request review
    Human->>GitHub: Approve or reject
```

**Gate:** Measured gain; regression checks; governed adoption.

## Cross-workflow architecture

```mermaid
sequenceDiagram
    participant Source as GitHub or Monitor
    participant Bus as Event Bus
    participant Factory as Orchestrator
    participant Agent
    participant Gate
    Source->>Bus: Domain event
    Bus->>Factory: Deliver event
    Factory->>Factory: Load durable work state
    Factory->>Agent: Execute selected workflow
    Agent->>Gate: Submit evidence
    alt Passed
        Gate-->>Factory: Advance state
        Factory->>Source: Publish result or next event
    else Failed or blocked
        Gate-->>Factory: Findings
        Factory->>Agent: Repair or escalate
    end
```

| Component | Responsibility |
| --- | --- |
| Event Bus | Distribute GitHub, CI, alert, and schedule events |
| Orchestrator | Route work, coordinate workflows, recover failed runs |
| Workflow | Define steps, transitions, gates, completion |
| Agent | Execute task-specific reasoning and actions |
| Gate | Enforce test, evidence, security, or approval policy |
| Ledger | Persist work identity, state, attempts, artifacts, evidence, decisions |
| Human Inbox | Collect approvals and blocked-work answers |
| Scheduler | Start maintenance and evaluation workloads |

### Required constraints

- **MUST persist workflow state.** Rationale: survive failures and restarts.
- **MUST handle repeated and out-of-order events safely.** Rationale: webhook delivery and retries can produce duplicates; use idempotency and commit/version checks.
- **MUST enforce mandatory gates outside prompts, even if skipping is faster.** Rationale: agents cannot self-bypass policy.
- **MUST preserve evidence and recovery state, even if cleanup is simpler.** Rationale: diagnose failures, protect incomplete work.
- **MUST authorize production actions by risk and permissions.** Rationale: investigative access does not grant destructive or deployment authority.
- **MUST track work-item correlation across workflows.** Rationale: connect issues, PRs, releases, incidents, and prevention actions.
- **MUST distinguish verified completion from agent-reported completion.** Rationale: deterministic gates need concrete receipts.

## Recommended adoption in antshc/loop

| Priority | Workflows |
| --- | --- |
| P1 — Core | Intake, Specification, Development, PR Review, PR Babysitting, Merge |
| P2 — Delivery | CI Recovery, Release, Production Bug Remediation |
| P3 — Operations | Incident Response, Security, Dependencies |
| P4 — Evolution | Technical Debt, Documentation, Postmortem, Factory Self-Improvement |

Adaptation for the existing Loop design:

- Preserve `dev` and PR babysitting as composable workflows, not hard-coded stages in a single pipeline.
- GitHub issues, PRs, checks, comments, and direct user requests should become normalized events.
- Keep the `hitl` label as a blocking policy; eligible work proceeds automatically. Ask questions in issue/PR comments only when genuinely blocked.
- Automatically break down actionable specifications into tickets; reuse existing subtickets.
- Keep Git worktree management in the Loop Git service; leave scheduling, isolation policy, merge retries, and recovery orchestration to workflows.
- Production mitigation and factory self-modification require explicit policy and evidence review.

**Recommended first architecture increment:** event normalization → persistent work ledger → workflow routing → existing dev and PR babysitting workflows → deterministic gate results.

## Primary sources

1. [Warp — Factories overview](https://docs.warp.dev/factories/)
2. [Warp — How Factories work](https://docs.warp.dev/factories/how-factories-work/)
3. [Warp — Factory automations](https://docs.warp.dev/factories/automations/)
4. [Warp — Measure and improve](https://docs.warp.dev/factories/measure-and-improve/)
5. [Factory — Software Factory overview](https://docs.factory.com/software-factory/overview)
6. [Factory — Triage](https://docs.factory.com/software-factory/triage)
7. [Factory — Release](https://docs.factory.com/software-factory/release)
8. [Factory — Incident response](https://docs.factory.com/software-factory/incident-response)
9. [Factory — AutoWiki](https://docs.factory.com/software-factory/wiki/overview)
10. [GitHub — Deploying code and merge gates](https://docs.github.com/en/pull-requests/concepts/deploying-code)
11. [GitHub — Copilot PR review and feedback](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/use-code-review)
12. [GitHub — Dependabot security updates](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependabot-security-updates)
13. [Google SRE — Incident response](https://sre.google/workbook/incident-response/)
14. [Google SRE — Canarying releases](https://sre.google/workbook/canarying-releases/)
15. [Google SRE — AI for reliable operations](https://sre.google/resources/practices-and-processes/ai-engineering-reliable-operations/)

**Bottom line:** The factory is a collection of durable, event-driven workflows connected by a shared state model, enforceable gates, and feedback from production.
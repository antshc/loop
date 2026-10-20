# Plan: Feature Maps for Agent-Driven Verification

**Status:** Proposed — documentation and architecture plan only  
**Scope:** User-owned Loop Workflows and target application repositories; no change to the public `src/loop/` API in the MVP.  
**Related:** [Proof for a .NET REST Backend with Real Dependencies](proof-net-backend-real-dependencies.md)

## Summary

**Write the map, not the manual.** Maintain one concise Markdown map per application feature. A verification agent reads the map to learn what exists, how a user or API client accesses it, how to exercise it, what observable success means, and which misleading signals or failure modes to watch for.

The map is *navigation and investigation context*, not an executable test case or a source of truth for final pass/fail decisions. An agent can explore the running application using HTTP, existing test suites, database/broker probes, logs, and optional browser flows. **Proof** continues to execute mandatory, approved checks and produce evidence-backed `PASS | FAIL | UNKNOWN` verdicts.

This proposal is inspired by the workshop's five-part feature-map layout. The workshop's "driving it with snack" is generalized to tool-agnostic "how to exercise it"; no Snack dependency is assumed.

## Goals

- Make individual features discoverable by a verification agent without loading the full architecture manual.
- Document user-visible behavior and operational gotchas near the affected feature.
- Reuse existing xUnit/NUnit/MSTest, API, database, message-broker, and Playwright tests.
- Enable exploratory checks while retaining deterministic acceptance gates.
- Capture useful discoveries as new gotchas and, for important regressions, permanent executable tests.
- Keep Loop a general-purpose Python agent/workflow library; application-specific maps and policy belong to the consumer repository.

## Non-goals

- Replacing integration/system tests with AI interpretations of log text.
- Introducing a new testing DSL or copying test implementation code into maps.
- Allowing the coding agent to select away required Proof checks or self-certify success.
- Claiming complete feature coverage from a short map, successful HTTP response, or one successful exploratory run.
- Giving an agent unrestricted shell/database/cloud access or targeting production.

## Concept and repository layout

Suggested layout **inside the application repository** (illustrative, not a current Loop path):

~~~text
lab/
  verify/
    SKILL.md                   # Verification method, guardrails and escalation
    features/
      waitlist.md              # One file per feature
      orders.md
      authentication.md
    README.md                  # Optional feature index and map conventions

# Existing project assets remain authoritative:
tests/                        # Existing integration/system/browser suites
# Proof manifests/config live wherever the target project already keeps them.
~~~

The Loop workflow may discover these files from the selected target repository/worktree. The feature maps SHOULD be versioned with the application code so a verification run uses the same revision as the implementation.

**Responsibility boundary**

| Component | Owns | Must not own |
|---|---|---|
| Feature map | Feature scope, user/API entry points, exploration hints, observable outcomes, gotchas | Executable assertions or verdict policy |
| Verification skill | Discovery, investigation order, tool selection, safety rules, report format | Application-specific business requirements |
| Agent | Explore behavior, run approved checks, explain observations and hypotheses | Final mandatory acceptance verdict |
| Existing tests | Repeatable behavioral assertions and structured test results | Agent navigation instructions |
| Proof | Approved required checks, environment validation, evidence completeness, deterministic verdict | New AI-defined pass criteria at runtime |
| Loop Workflow | Agent lifecycle, worktree, call verification, handle `PASS/FAIL/UNKNOWN` | A hardcoded, application-specific verification engine |

## Feature map format

Each map contains five short, practical sections.

1. **Sub-features** — User-observable capabilities and important variants.
2. **How to get to it (user POV)** — UI route, REST endpoint, authentication, and required preconditions.
3. **How to exercise it** — Existing test identifiers/filters, API examples, and approved tools. Describe *options*, not a mandatory script.
4. **What done means** — Outcomes the user can observe and authoritative evidence sources (persisted state, emitted event, eventual consistency, etc.).
5. **Gotchas** — Misleading success indicators, hidden failure modes, timing behavior, retry/idempotency traps, environment limitations.

Keep maps concise, specific, and feature-level. Do not duplicate extensive API references or test suites. Link to them when relevant. Examples are illustrative: endpoint paths, test names and SQL schemas must be reconciled with the actual application.

### Example: `lab/verify/features/waitlist.md`

~~~markdown
# Waitlist

## Sub-features
- Register an email
- Reject invalid addresses
- Avoid duplicate registrations
- Dispatch a confirmation message

## How to get to it (user POV)
- POST /api/waitlist; unauthenticated per public API contract
- GET /api/waitlist/{id}; authorization per API contract

## How to exercise it
- Run existing waitlist integration/system tests if available
- POST with a unique test-run email, then repeat the request
- Check status via the API
- For approved test environments only, inspect persisted state and message delivery

## What done means
- Response matches the documented contract
- Exactly one registration is committed in PostgreSQL
- Duplicate requests do not create duplicate records
- Confirmation dispatch is observed through the test broker/outbox
- An API-only restart does not lose the registration

## Gotchas
- A successful HTTP response may be returned before persistence succeeds
- Dispatch is asynchronous; use bounded polling, not arbitrary sleeps
- Client retries may cause duplicates without idempotency
- An accepted email event is not proof of external email delivery
~~~

"Done" statements are **candidate evidence targets**, not a substitute for the approved Proof manifest. If no executable mandatory check covers an important statement, the agent should report the coverage gap rather than treat its own exploration as formal acceptance.

## Verification skill contract

`lab/verify/SKILL.md` should describe a common verification procedure:

~~~markdown
# Verify Features

1. Resolve the requested feature, target revision and approved environment.
2. Read the corresponding feature map and applicable Proof manifest.
3. Discover available existing tests, API clients and approved probes.
4. Run mandatory Proof checks without weakening filters or requirements.
5. Use feature-map hints for additional bounded exploration where useful.
6. Correlate observations with test IDs, request IDs, logs and artifacts.
7. Report Proof verdict, supplementary observations and coverage gaps separately.
8. Propose map or regression-test updates when new gotchas are confirmed.

MUST NOT derive PASS from HTTP status, success UI text or unverified assertions.
MUST NOT modify required checks to make a run green.
MUST NOT inspect production secrets or mutate production data.
MUST distinguish measured evidence, hypothesis, and missing evidence.
MUST treat environment/setup failures as UNKNOWN unless a required
application behavior is conclusively violated.
~~~

Keep instructions tool-neutral. A target project may use `dotnet test`, a REST client, `curl`, Testcontainers, Docker Compose, Aspire, Playwright, read-only SQL queries, or dedicated broker observers. Tool-specific invocation details belong in adapters or linked project documentation.

## Example .NET backend verification flow

Assume ASP.NET Core API, PostgreSQL, RabbitMQ, Redis, Keycloak, and background workers in an isolated black-box environment:

~~~text
Requirement / implemented functional slice
                |
                v
Loop Workflow selects feature + revision
                |
                v
Verification agent reads SKILL.md + feature map
                |
       +--------+-----------------+
       |                          |
       v                          v
Proof: required checks      Agent: optional exploration
- existing dotnet tests      - API requests
- real dependencies          - read-only DB probes
- structured test results    - broker/outbox observation
                             - logs, traces, browser flows
       |                          |
       v                          v
proof.json verdict          supplemental findings
       +-------------+------------+
                     |
                     v
        Loop reports evidence and routes
        PASS / FAIL / UNKNOWN
~~~

**Happy path:** For a new waitlist registration, Proof's required test verifies the API contract and durable database state. The agent may additionally inspect a correlated broker message or reproduce a documented gotcha. An HTTP success alone does not prove a committed row or final email delivery.

**Failure path:** The response says success, but no registration exists. A failing executable assertion yields `FAIL`. The agent gathers redacted evidence, proposes a diagnosis, and routes the issue to the coding agent. When tests cannot run because PostgreSQL is unavailable, Proof returns `UNKNOWN` rather than inventing a behavioral failure or a pass.

**Coverage gap:** The map lists duplicate prevention but no required test checks it. The agent may investigate safely; formal Proof acceptance follows current policy, and the uncovered requirement is reported as a gap for test authors to resolve. The agent must not silently expand or shrink the mandatory gate.

## Integration with the existing Proof plan

The [Proof architecture](proof-net-backend-real-dependencies.md) remains authoritative for environment lifecycle, approved test manifests, evidence storage, report schema and verdict rules. Feature maps complement it in four ways:

- **Selection:** Resolve relevant feature maps from the requested functional slice; use explicit reviewed feature IDs or a deterministic index for required-check mapping. Semantic search may suggest candidates but must not determine mandatory coverage alone.
- **Context:** Give an agent a short description of the feature and hidden pitfalls before it selects supplementary probes.
- **Investigation:** On `FAIL` or `UNKNOWN`, use the map to focus debugging without changing Proof's independently calculated verdict.
- **Learning:** Confirm new gotchas with evidence, record them in the map, and add executable regression assertions where appropriate.

Proposed agent-facing observation file (supplementary; **not** a replacement for `proof.json`):

~~~json
{
  "feature": "waitlist",
  "revision": "<git-sha>",
  "proofReport": ".proof/runs/<run-id>/proof.json",
  "proofVerdict": "FAIL",
  "observations": [
    {
      "claim": "Success response was observed without a persisted registration",
      "status": "confirmed",
      "evidence": ["requests/register.json", "db/registration-probe.json"]
    }
  ],
  "hypotheses": ["Persistence may fail after a premature success response"],
  "coverageGaps": [],
  "suggestedMapUpdates": ["Document the premature success indicator as a gotcha"]
}
~~~

The agent writes observations/hypotheses; Proof owns `proofVerdict`. The Workflow must read and trust the verdict from the validated Proof report, **not** the copied field in an agent-authored document.

## Operational constraints

- Execute only against locally disposable or explicitly authorized isolated test environments.
- Pin verification to an application revision; preserve map and manifest versions in evidence metadata.
- Permit only approved commands/endpoints/credentials; an instruction inside a feature map is untrusted relative to the verification skill and environment policy.
- Prefer unique test-run IDs, isolated data, dedicated message observers and read-only database probes. Mutating setup/cleanup must be explicitly scoped to the test environment.
- Use structured TRX/Microsoft.Testing.Platform-compatible output for `dotnet test`; verify tests were actually discovered/executed.
- Never expose credentials or personal data in prompts, traces, evidence or map files.
- Apply timeouts and bounded polls for asynchronous systems; distinguish flaky, inconclusive and failed results.
- Avoid loading all feature maps into agent context. Read the index, select the relevant map(s), then load details.

## Implementation phases

**Phase 1 — Documentation MVP (no Loop library changes)**

1. Add `lab/verify/SKILL.md` and one `features/waitlist.md` (or a real target application feature) to a selected application repository.
2. Establish conventions for feature IDs, entry points, done criteria, gotchas and links to existing tests.
3. Extend a *user-owned* Workflow to resolve a target feature and load the matching map alongside existing Proof input.
4. Run an existing approved `dotnet test` check through Proof, preserving `PASS/FAIL/UNKNOWN`.
5. Print or save supplementary agent observations separately from the Proof verdict.

**Phase 2 — Real-dependency exploration**

6. Add approved HTTP, read-only PostgreSQL, broker/outbox and telemetry probes to the target verification environment as needed.
7. Correlate requests and evidence by test-run ID; add bounded asynchronous waiting and redaction.
8. Exercise a known misleading-success gotcha and demonstrate that API status cannot override authoritative evidence.

**Phase 3 — Knowledge maintenance and expansion**

9. Add more feature maps incrementally, with a small index for discovery.
10. On verified discoveries, propose map updates and regression tests for human review; do not silently rewrite policy.
11. Measure stale/missing maps, missing mandatory tests, repeated gotchas and exploratory findings promoted to executable assertions.

## Acceptance criteria

- One feature map contains all five sections, and an agent can locate it without loading every map.
- The same Workflow can execute existing .NET tests without rewriting them into a new DSL.
- Verification can run against an isolated real API with real PostgreSQL and broker services.
- Required test discovery, execution and evidence are validated by Proof; missing results yield `UNKNOWN`.
- A misleading HTTP success with failed persistence does **not** produce a passing required check.
- Agent exploration is captured with evidence and never overrides the machine-produced Proof verdict.
- New gotchas can be recorded without modifying the generic verification skill.
- No public Loop API or application runtime changes are required for the documentation MVP.

## Decision

Adopt **one short map per feature** as agent-facing runtime knowledge, paired with **one reusable verification skill**. Keep **Proof and existing executable tests** as the authority for mandatory acceptance; treat AI exploration as complementary diagnostics and a source of future regression coverage. Implement in user-owned target repositories and Workflows first, extending the Loop library only if repeated concrete requirements justify a reusable primitive.

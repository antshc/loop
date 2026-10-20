# Proof for a .NET REST Backend with Real Dependencies

**Status:** Proposed architecture  
**Audience:** Backend engineers, test engineers, and AI-agent platform developers  
**Purpose:** Reuse existing .NET system and integration tests as an independent, evidence-based verification mechanism for AI-assisted development.

## 1. Summary

**Proof is a verification orchestrator, not a new test framework.** It starts or targets a real backend environment, executes the project's existing tests and probes, collects evidence, and emits a stable machine-readable verdict that an AI coding agent can act upon.

The core loop is:

```text
Human-defined requirement / acceptance criteria
                |
                v
AI agent implements a functional slice
                |
                v
Proof selects required, predeclared checks
                |
                v
Real API + real dependencies + existing test suites
                |
                v
Evidence: test results, HTTP exchanges, logs, traces, artifacts
                |
                v
proof.json: PASS | FAIL | UNKNOWN, checks, evidence, next
                |
       +--------+---------+
       |        |         |
      PASS     FAIL     UNKNOWN
       |        |         |
    Complete  Fix app   Fix/investigate verification setup
```

**Fundamental separation:** Tests decide whether behavior meets expectations. Proof decides whether sufficient trustworthy evidence exists, normalizes the results, and reports them to the orchestrator. The AI coding agent consumes the result; it does not decide its own verdict.

## 2. Goals and non-goals

### Goals

- Run *existing* API, database, messaging, integration, system, and optional browser tests without rewriting them.
- Exercise the deployed or running application through its real public interfaces wherever possible.
- Verify integrations against real dependency implementations in an isolated environment.
- Return an unambiguous `PASS`, `FAIL`, or `UNKNOWN` with individual check results.
- Retain actionable, bounded evidence so an agent can diagnose a failure efficiently.
- Support local iteration, CI, and a dedicated staging environment through the same proof contract.
- Make required verification checks independent of the coding agent's preferences.

### Non-goals

- Replacing xUnit, NUnit, MSTest, Playwright, Testcontainers, Docker Compose, or Aspire.
- Reimplementing business assertions inside Proof.
- Automatically proving the complete correctness of a backend from a green test suite.
- Letting an LLM invent a `PASS` from logs or missing evidence.
- Running destructive tests against production infrastructure.

## 3. Reference backend and real dependency topology

Use an illustrative **Orders API**, implemented in ASP.NET Core and running as an actual process/container with Kestrel:

```text
                       Proof runner
             +-------------+-------------+
             |             |             |
          HTTP API       SQL probe    Browser (optional)
             |             |             |
             v             |             |
       +-------------+     |             |
       | Orders API  |-----+             |
       | ASP.NET Core|                   |
       +------+------+                   |
              |                          |
     +--------+----------+----------+    |
     |        |          |          |    |
     v        v          v          v    v
 PostgreSQL RabbitMQ    Redis    Keycloak  Frontend
 (records) (events)    (cache)    (OIDC)  + Playwright

               Logs / traces / metrics
                         |
                   Evidence store
```

**What “real” means:** The application runs its actual startup, middleware, migrations, HTTP listener, authentication, persistence, message-publishing code, and background services. PostgreSQL, RabbitMQ, Redis, and Keycloak are running implementations, not in-memory mocks. Use the same relevant product and major versions as production where feasible. This is *production-like*, not necessarily production-identical.

| Dependency or component | What Proof can use | Example proof |
|---|---|---|
| REST API (Kestrel) | Actual deployed process/container, HTTP requests | Status codes, contracts, authentication, idempotency |
| PostgreSQL / SQL Server | Real isolated database and schema migrations | Row persisted, transaction committed, constraint enforced |
| RabbitMQ / Azure Service Bus | Actual broker, isolated namespace/queues | Event published, consumer processes it, retry/dead-letter behavior |
| Redis | Real server | Cache invalidation, TTL and consistency behavior |
| Keycloak / other OIDC IdP | Real test realm, client and accounts | Obtain token, verify claims, reject unauthorized action |
| Object storage | Real S3/Blob test account or explicitly labeled compatible emulator | Upload/download, metadata, lifecycle assumptions |
| Background workers | Real worker process | Job completion, scheduled processing, outbox delivery |
| Other HTTP services | Provider sandbox or dedicated test instance | Outgoing contract and error handling |
| Browser/frontend | Real UI and Playwright | End-to-end workflow, screenshot and trace |
| Observability | Application logs and OpenTelemetry traces | Correlate a failed scenario with a request and downstream calls |

**Dependency fidelity:** Real PostgreSQL in a container is the actual database engine; an S3-compatible implementation or a cloud-service emulator is **not** the corresponding managed cloud service. Mark emulated dependencies explicitly and add staging tests against real managed services when provider-specific semantics matter.

## 4. Environment execution models

| Mode | Environment | Best for | Main limitation |
|---|---|---|---|
| **A. .NET integration** | `WebApplicationFactory` + Testcontainers | Fast tests with real DB/broker and application DI | In-process `TestServer` does not prove the deployment/container startup path |
| **B. Black-box system** | Docker Compose, Aspire AppHost, or Kubernetes: actual API + actual dependencies | Real HTTP listener, service wiring, restart and background-worker behavior | Slower; needs environment provisioning |
| **C. Cloud staging** | Dedicated deployed app and real managed dependencies | IAM, TLS, DNS, cloud services, networking, deployment | Cost, slower setup, strict isolation and credentials required |

**Recommended default:** Mode B for functional-slice Proof, with Mode A for fast feedback and Mode C for cloud-specific risks. A project can use all three; the proof manifest explicitly selects its mode. If existing tests own their Testcontainers lifecycle, Proof must **not** also provision duplicate resources; each dependency has one defined lifecycle owner.

## 5. What Proof actually executes

Proof should have small adapters, not its own assertion engine:

1. **Test adapter:** Runs `dotnet test` with existing category filters; reads TRX (VSTest) or the project's configured Microsoft.Testing.Platform report format. Does not infer success from exit code alone: verifies that the expected tests were discovered and executed.
2. **HTTP adapter:** Runs existing API scenario collections or dedicated HTTP-based checks; records redacted request/response metadata and assertions.
3. **Database adapter:** Runs read-only probes of expected persisted state, migrations, or transactional outcomes, where system tests do not already cover them.
4. **Messaging adapter:** Uses a dedicated observation queue/subscription or test-run correlation key to verify published events; never steals production consumer messages.
5. **Browser adapter (optional):** Runs existing Playwright tests and collects screenshots/traces.
6. **Telemetry adapter:** Retrieves application/container logs and OpenTelemetry traces for the specific test run.

An adapter invokes the existing test system and translates structured output. An adapter is not permitted to quietly convert failures or missing tests into `PASS`.

### Proposed manifest (illustrative—not an existing Proof CLI format)

```yaml
id: orders-create-and-retrieve
mode: black-box
requires:
  - api
  - postgres
  - rabbitmq
  - redis
  - keycloak

checks:
  - id: api-contract
    runner: dotnet-test
    project: tests/Orders.ApiTests/Orders.ApiTests.csproj
    filter: "Proof=orders-create-and-retrieve"
    required: true

  - id: order-system-flow
    runner: dotnet-test
    project: tests/Orders.SystemTests/Orders.SystemTests.csproj
    filter: "Proof=orders-create-and-retrieve"
    required: true

  - id: browser-order-flow
    runner: playwright
    spec: tests/browser/order-flow.spec.ts
    required: false

artifacts:
  directory: .proof/runs
```

The *required* checks and dependency list are version-controlled and reviewed alongside requirements. Supplementary checks may be added by the agent, but cannot replace mandatory checks.

## 6. Concrete scenario: create an order with real dependencies

**Acceptance criteria:** An authenticated request creates an order, saves it, emits exactly one `OrderCreated` event, and the order survives an API restart.

**Prepare environment**

1. Provision isolated PostgreSQL, RabbitMQ, Redis, and Keycloak; launch the real API and worker processes.
2. Run the application's normal database migrations and register an isolated Keycloak realm/client/test user.
3. Wait for dependency health and API readiness; verify real authentication and database connectivity.
4. Assign a unique `proof_run_id` / correlation ID. Create dedicated queue/subscription and test data, where needed.

**Execute existing system test**

1. Obtain an access token from the real OIDC provider using an appropriate non-interactive test flow.
2. `POST /orders` with that token and a unique idempotency key; assert `201 Created` and the response contract.
3. `GET /orders/{id}`; assert the returned order matches the requested product/quantity/status.
4. Query the real PostgreSQL database to assert the committed row, where authorized by the test design.
5. Inspect a dedicated RabbitMQ subscriber or event outbox to assert one expected `OrderCreated` event with matching correlation ID.
6. Restart **only the API**, keeping PostgreSQL and broker state; query `GET /orders/{id}` again.
7. Repeat the request using the same idempotency key; assert no duplicate order/event was created.
8. Collect HTTP, test-run result, broker/DB evidence, API logs, and relevant traces.

The scenario should verify outcomes, not internal implementation details. Prefer bounded polling for asynchronous messaging over arbitrary sleeps. Verify both the presence of the expected event and absence of duplicates over a declared observation window.

### Example: existing test command

For a project using the VSTest `dotnet test` runner and TRX logger:

```bash
dotnet test tests/Orders.SystemTests/Orders.SystemTests.csproj \
  --filter "Proof=orders-create-and-retrieve" \
  --logger "trx;LogFileName=orders.trx" \
  --results-directory .proof/runs/RUN_ID/tests
```

If the project uses **Microsoft.Testing.Platform**, use its supported reporting extension/options rather than assuming the VSTest logger syntax is valid. The test itself may manage dependencies through Testcontainers, or connect to the independently provisioned proof environment: choose **one** ownership model per run.

## 7. Verification lifecycle

```text
1. Resolve revision, scenario, approved manifest
2. Validate permissions, targets, and non-production safety
3. Allocate run ID and artifact directory
4. Provision/start environment (if Proof owns it)
5. Check dependencies, migrations, auth, readiness
6. Execute selected required test suites
7. Collect structured test output + evidence
8. Classify each check and overall verdict
9. Generate deterministic next-step hints
10. Publish proof.json; clean up isolated resources
```

Preflight failure does not automatically mean the application is defective. Proof must identify whether it owns the missing dependency or whether the feature under test is expected to establish connectivity. Cleanup runs even after failure and must not erase evidence before retention/publishing.

## 8. Result contract

A stable JSON envelope enables both human inspection and machine consumption:

```json
{
  "schemaVersion": "1.0",
  "runId": "run-20261018-001",
  "scenario": "orders-create-and-retrieve",
  "revision": "d14af25cd65a",
  "environment": {
    "mode": "black-box",
    "dependencies": {
      "postgres": "real-container",
      "rabbitmq": "real-container",
      "redis": "real-container",
      "keycloak": "real-container"
    }
  },
  "verdict": "FAIL",
  "checks": [
    {
      "id": "create-order",
      "verdict": "PASS",
      "expected": "HTTP 201",
      "actual": "HTTP 201",
      "evidence": ["requests/create-order.json"]
    },
    {
      "id": "order-survives-api-restart",
      "verdict": "FAIL",
      "expected": "HTTP 200",
      "actual": "HTTP 404",
      "evidence": ["tests/orders.trx", "logs/api.log"]
    }
  ],
  "next": [
    "Investigate why GET /orders/{id} returns 404 after an API-only restart.",
    "Inspect persistence and database connection evidence before changing code."
  ],
  "artifacts": ["tests/orders.trx", "logs/api.log", "traces/order.json"]
}
```

### Verdict rules

- **PASS:** Every *required* check was actually discovered, executed, and passed; all mandatory evidence is present and valid.
- **FAIL:** There is conclusive evidence that at least one required behavior was violated, even if other checks could not complete. Record incomplete checks separately.
- **UNKNOWN:** No conclusive failure is established, but verification cannot establish success (missing tests/evidence, setup failure, timeout, unavailable test infrastructure, or inconclusive outcome).
- Optional checks are recorded, but do not change the mandatory verdict unless a policy explicitly elevates them.
- `0 tests discovered`, a missing report, missing artifacts, or an unchecked requirement must never become `PASS`.

Where relevant, classify a failed check's `failureCategory` as `application`, `environment`, `test-harness`, or `inconclusive`; preserve raw evidence so classifications can be audited.

### Who writes `next`?

The **Proof report generator** writes `next`. Prefer deterministic mappings from known assertions and failure categories, created by the test authors. These messages describe the observed mismatch and a reasonable investigation direction, **not an unproven root cause**. A separate diagnostic AI agent may optionally expand the hints, but its suggestions are labeled hypotheses and cannot change the verdict.

## 9. Evidence and operational safeguards

- Save the commit SHA, environment/configuration identifiers, dependency versions, executed test IDs/counts, timestamps, and raw test results.
- Store redacted HTTP metadata, database assertion summaries, sanitized logs, OpenTelemetry trace IDs, messaging observations, and optional Playwright traces/screenshots.
- Correlate artifacts via test-run ID and propagated request trace/correlation ID.
- Preserve artifacts on failures and unknown outcomes; apply bounded retention policies.
- **Never** store access tokens, connection-string secrets, sensitive payloads, or production personal data in proof artifacts.
- Run against disposable local resources or explicitly approved isolated staging resources only.
- Restrict adapters to approved commands, service endpoints, and credentials; do not let an agent execute arbitrary shell commands through Proof.
- Apply per-run test data namespaces, unique IDs, queue isolation, and cleanup so parallel agents cannot contaminate each other's runs.
- Declare timeouts, retry budgets, and flakiness handling. A test that passes only after retries should retain the retry history.

## 10. Integration with an AI coding agent

**Human / product owner:** Defines expected behavior and approves mandatory checks.

**System/integration tests:** Exercise real behavior; produce objective assertions and artifacts.

**Proof service/CLI:** Orchestrates tests; evaluates evidence completeness; returns verdict and hints.

**Coding agent:** Implements and investigates; may add tests, but cannot silently reduce required coverage.

**Loop orchestrator:** Calls Proof after each completed functional slice and routes the outcome:

```python
proof = verifier.verify(scenario="orders-create-and-retrieve")

match proof.verdict:
    case "PASS":
        finish_slice()
    case "FAIL":
        send_evidence_to_coding_agent(proof)
    case "UNKNOWN":
        diagnose_verification_setup(proof)
```

The orchestrator should cap retries, recognize repeated identical failures, and escalate when the fix requires infrastructure credentials, a product decision, or human access.

## 11. Implementation plan

**Phase 1 — Minimal Proof adapter (MVP)**

- Select a single existing xUnit/NUnit/MSTest backend system-test project.
- Map one functional slice to a fixed test filter and expected test count/IDs.
- Execute the existing suite through `dotnet test`; parse its structured output.
- Produce `proof.json` with required check results, overall verdict, and evidence paths.
- Implement `UNKNOWN` for absent results, zero discovery, broken setup, and incomplete evidence.
- Integrate `proof.json` as the Loop orchestrator's verification result.

**Phase 2 — Real environment orchestration**

- Add Docker Compose, Testcontainers, or Aspire integration according to existing project conventions.
- Start real API + PostgreSQL + broker + Redis + Keycloak; establish readiness and cleanup.
- Support isolated test IDs, persisted data and API-only restarts.
- Collect logs, database assertions, broker observations, and traces.

**Phase 3 — System and browser coverage**

- Reuse broader API/system test suites and optional Playwright scenarios.
- Add cloud staging tests for IAM, service-specific semantics, DNS, TLS, managed queue/storage behavior.
- Add deterministic failure mappings, evidence retention, trend reporting, and optional AI diagnostic suggestions.

### MVP acceptance criteria

- Existing .NET system test suites run unchanged against supported real dependencies.
- Required tests cannot be skipped without producing `UNKNOWN` or an explicit failed policy check.
- A real application regression produces `FAIL` with evidence and a specific investigation hint.
- Broken dependencies or lost test results produce `UNKNOWN` rather than a false `PASS`.
- A clean run produces `PASS` with reproducible revision and artifact metadata.
- The agent receives one small machine-readable report rather than unbounded console output.

## 12. Architecture decision

**Adopt Proof as a thin adapter over existing backend system and integration tests.** Prefer black-box verification with an actual running .NET API and real isolated dependency services for functional-slice acceptance. Reuse current .NET testing frameworks and infrastructure setup; add no new assertion DSL. Extend to cloud-specific staging and browser flows only where the required behavior cannot be established in the core environment.

## References

- [Microsoft: Integration tests in ASP.NET Core](https://learn.microsoft.com/en-us/aspnet/core/test/integration-tests?view=aspnetcore-10.0)
- [Testcontainers for .NET: ASP.NET Core](https://dotnet.testcontainers.org/examples/aspnet/)
- [Testcontainers for .NET: PostgreSQL module](https://dotnet.testcontainers.org/modules/postgres/)
- [Microsoft: Aspire distributed application testing](https://learn.microsoft.com/en-us/dotnet/aspire/testing/overview)
- [Microsoft: dotnet test (VSTest)](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-test-vstest)
- [Microsoft: dotnet test (Microsoft.Testing.Platform)](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-test-mtp)
- [Playwright .NET: Trace viewer](https://playwright.dev/dotnet/docs/trace-viewer)
- [OpenTelemetry .NET: Log correlation](https://opentelemetry.io/docs/languages/dotnet/logs/correlation/)
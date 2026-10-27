# Test Strategy: Unit Tests Against Fakes

## Purpose

Loop's behavior is spread over the shipped library (agent builder, CLI runners, git, hooks, sessions) and user-owned Workflow scripts, and every real boundary (`git`, `gh`, the agent CLIs, Docker) is slow, stateful, or unavailable in CI. This concept fixes where the real/stand-in line sits and how a Workflow's behavior is proven end to end.

## Approach

One test group runs in the project's single test command (`bun run test`: ESLint, the TypeScript compiler, then `bun test`). It tests one component or one Workflow rule at a time against doubles injected at the process boundaries; a Workflow scenario is driven through the Workflow's `main(argv, dependencies)` over the same doubles, so the real builder, prompt rendering, response extraction, and Workflow logic cooperate.

- **Library seams:** `CliRunner`, `GitService`, and `DockerService` are the replaceable boundaries; tests build `new AgentBuilder(runner, git, docker)` over recording doubles (`tests/unit/support/doubles.ts`: `RecordingRunner`, `RecordingGit`, `RecordingDocker`).
- **Workflow seams:** a Workflow's Git reads and publication (`GitService`) and its GitHub client (`FakeGh` behind `gh`) are injected as in-memory fakes; each Workflow's composition root binds its seams by token, so a test passes the doubles as `main`'s dependencies ([Compose dependencies with InversifyJS](../adr/compose-dependencies-with-inversifyjs-constructor-injection-and-a-composition-root-per-workflow.md)).
- **Optional-module, output, and log seams:** an injected module loader replaces the dynamic import of the telemetry packages, an injected output stream with a TTY flag replaces console patching for the visualization, and an in-memory log sink captures records. Monkeypatching globals is a last resort where no injectable seam exists.
- **Ticket delegate seam:** reusable Ticket orchestration is tested through `deliverTickets` with function delegates returning typed results; multi-repository scenarios drive the dev `main` with separate histories, clients, and delegates to prove target isolation and harness-only Ticket updates.
- **Workflow-local harness:** `workflows/dev/tests/support/harness.ts` holds dev's fakes, `DevHarness`, and response-envelope builders; the dev tests import no sibling Workflow harness.
- **Per-test state:** every test that touches disk creates its own temporary directory and removes it afterwards; no test shares or reads the user's files.
- **Architecture checks:** dependency-cruiser runs inside the suite against `tests/architecture/contracts.ts`.

## Rules

- MUST test every shipped component against its own boundary double, one behavior per test.
- MUST NOT start a real `gh`, `docker`, or agent CLI process, or reach the network, from any test. Worktree lifecycle and PR publication recovery tests may use isolated temporary Git repositories and local bare remotes; they MUST NOT access user repositories or network remotes.
- MUST drive a Workflow scenario through the Workflow's `main` with injected dependencies, with the real builder, prompt rendering, and response extraction.
- MUST make the runner double or injected Ticket delegate commit through the Git double before reporting completion, so Git validation runs against fake state.
- MUST exercise agent response parsing with raw runner output; tests of the independent Ticket delegate seam may return typed results without parsing an agent envelope.
- MUST assert a scenario outcome on effects at the fake boundaries — tracker writes, pushes, CLI invocations and prompts — and MUST NOT assert on Workflow internals.
- MUST state each scenario as Given/when/then in a comment or `describe` name, and name each test as a sentence of the behavior it proves.
- MUST keep doubles and Workflow wiring in the owning Workflow's test support; a test file MUST NOT rebuild them.
- MUST verify persisted and emitted formats (PR state, scheduler registry, dev execution log, settings, Copilot session log, event streams, log records, span and metric names and attributes, hook payload) against golden fixtures under `tests/fixtures/` and each Workflow's own `tests/fixtures/`.
- MUST run the architecture checks inside the test run, and MUST prove each contract with a seeded violation that fails it.
- MUST keep test-only doubles out of the public `loop` API; the dry-run fakes (`FakeCliRunner`, `DirectoryGit`, `fakeAgent`) ship because `--fake` and `--dry` runs use them.

## Architecture checks

`tests/architecture/contracts.ts` declares every contract as dependency-cruiser rules: the library layering (generated as one rule per layer boundary from a single ordered layer list), forbidden imports, Workflow independence, Ticket orchestration importing no agent delegate, Workflows importing only the public `src/loop/index.ts`, and only the telemetry adapter (`src/loop/observe/otel.ts`) importing the OpenTelemetry packages. Every contract also checks indirect imports (`reachable`), except the Workflows-to-public-`loop` contract, which is direct-only because the public entry point legitimately reaches private modules. `architecture.test.ts` fails on any violation in the repository; `seeded-violations.test.ts` writes a violating fixture for each contract and expects the contract to fail.

## Runtime compatibility

The library and example Workflows run on Bun only and pin the minimum Bun in package metadata (`engines.bun` and `packageManager` in `package.json`; `MINIMUM_BUN_VERSION` in `src/loop/runtime-guard.ts`). The suite runs on that minimum and on the latest Bun, each with its own installation:

```sh
bun --version
bun install --frozen-lockfile
bun run test
bun workflows/dev/main.ts --help
bun workflows/plan_implement.ts --help
```

Run from the repository root with `git` installed. The root command covers library tests, all four example Workflows, lint, type-check, and the architecture contracts. The `--help` checks exercise Workflow imports and command-line parsing without starting agents or contacting GitHub.

A type-check alone does not prove runtime compatibility: Bun APIs, dependency resolution, and error paths must also run on both versions. Node.js is unsupported and is not tested.

## Known gaps

- Signal delivery, process-group kill, and OS file locking are verified on Linux; macOS and Windows behavior is untested.
- Telemetry is verified against in-memory exporters and a golden span and metric contract; export to a real collector, including protobuf and gRPC, is untested, and the OpenTelemetry packages are not officially supported on Bun.
- Docker agent behavior under Bun is unspecified and untested.
- `pr_babysit`'s lock uses Bun's FFI, which Bun marks experimental.

## Example

A `dev` scenario drives `main` through `DevHarness`: the runner double commits through the Workflow Git double and answers with the raw response envelope, and the test asserts on tracker writes and publication.

```ts
const harness = new DevHarness(directory);
const code = await harness.run(() => harness.commitAndReport(10));
expect(code).toBe(0);
// then assert on harness.calls('issue', ...) and harness.git.pushedCount
```

## Implementation Map

| Concern | Stable anchor | Semantic locator |
|---|---|---|
| Workflow wiring for tests | One local harness binds dev to fakes at every boundary | `workflows/dev/tests/support/harness.ts`: `DevHarness`, `FakeRunner`, `FakeWorkflowGit`, `DirectoryWorktree` |
| Envelope builders | Response envelopes the Workflows extract | `workflows/dev/tests/support/harness.ts`: `envelope`, `COMPLETED` |
| Boundary fakes | One double per boundary | `workflows/dev/tests/support/fake-gh.ts`: `FakeGh`; `tests/unit/support/doubles.ts`: `RecordingRunner`, `RecordingGit`, `RecordingDocker` |
| Unit tests | Library and Workflow rules against doubles | `tests/unit/*.test.ts` and `workflows/dev/tests/*.test.ts`; discovered by root `bun test` |
| Golden fixtures | Persisted and emitted formats | `tests/fixtures/`, `workflows/dev/tests/fixtures/`, `workflows/pr_babysit/tests/fixtures/` |
| Package independence | Ticket orchestration imports no agent delegate; the named prompts render as the fixtures pin | `workflows/dev/tests/boundary.test.ts`, `workflows/dev/tests/prompts.test.ts` |
| Architecture checks | Import rules enforced as a test, each proven by a seeded violation | `tests/architecture/contracts.ts`, `architecture.test.ts`, `seeded-violations.test.ts` |

## References

- [Loop Library Workflow Architecture](str-loop-library-workflow-architecture.md)
- [Autonomous Spec Delivery](dom-autonomous-spec-delivery.md)
- [Agent Client](str-agent-client.md)
- [Port Loop and Workflows to TypeScript on Bun](../adr/port-loop-and-workflows-to-typescript-on-bun-in-one-cutover.md)

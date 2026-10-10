# Test Strategy: Unit Tests Against Fakes

## Purpose

Loop's behavior is spread over the shipped library (agent builder, CLI runners, git, hooks, sessions) and user-owned Workflow scripts, and every real boundary (`git`, `gh`, the agent CLIs, Docker) is slow, stateful, or unavailable in CI. This concept fixes where the real/stand-in line sits and how a Workflow's behavior is proven end to end.

## Approach

One unit test group runs in the project's single test command (`pytest`). It tests one component or one Workflow rule at a time against fakes at the process boundaries; a Workflow scenario is driven through the Workflow's `main` over the same fakes, so the real builder, prompt rendering, response extraction, and Workflow logic cooperate.

- **Library seams:** `CliRunner` and `GitService` (and `DockerService`) are the replaceable boundaries; tests build `AgentBuilder(runner, git, docker)` over a `FakeRunner` and `FakeGitService`.
- **Workflow seams:** a Workflow's Git reads and publication (`WorkflowGit`) and its GitHub client (`FakeGhCli`) are injected as in-memory fakes; the `dev` Workflow takes `new_agent`, so tests supply a builder over the fake runner.
- **Shared harness:** `tests/workflow_harness.py` holds the fakes, `DevHarness`, and the response-envelope builders, imported by every test module.
- **Architecture checks:** import-linter contracts run as a test.

## Rules

- MUST test every shipped component against its own boundary fake, one behavior per test.
- MUST NOT start a real `git`, `gh`, `docker`, or agent CLI process, or reach the network, from any test; the project has no live or integration test group.
- MUST drive a Workflow scenario through the Workflow's `main` with injected dependencies, with the real builder, prompt rendering, and response extraction.
- MUST make the runner fake commit through the Git fake before it reports completion, so Git validation runs against fake state.
- MUST NOT hand a Workflow a pre-built agent result: the runner fake returns the raw output the CLI would print, and the Workflow extracts the envelope.
- MUST assert a scenario outcome on effects at the fake boundaries — tracker writes, pushes, CLI invocations and prompts — and MUST NOT assert on Workflow internals.
- MUST state each scenario as Given/when/then in its docstring, and name each test as a sentence of the behavior it proves.
- MUST keep fakes and Workflow wiring in the shared harness; a test module MUST NOT rebuild them.
- MUST run the architecture checks (layering, workflows import only the public `loop` API, platforms import no Workflow) inside the test run.
- MUST keep test fakes out of the public `loop` API.

## Example

A `dev` scenario drives `main` through `DevHarness`: the runner fake commits through the workflow Git fake and answers with the raw response envelope, and the test asserts on tracker writes and publication.

```python
harness = DevHarness(tmp_path)
harness.runner.handler = harness.commit_then(lambda sha: envelope("Checkout|10", result={**COMPLETED, "commit": sha}))
# run dev.main over the harness, then assert on harness.writes() and harness.git.pushed_count
```

## Implementation Map

| Concern | Stable anchor | Semantic locator |
|---|---|---|
| Workflow wiring for tests | One harness binds the Workflow entry point to fakes at every boundary | `tests`: `DevHarness`, `FakeRunner`, `FakeGitService`, `FakeWorkflowGit` |
| Envelope builders | Response envelopes the Workflows extract | `tests`: `plan_envelope`, `implement_envelope`, `envelope` |
| Boundary fakes | One fake per boundary | `tests`: `FakeRunner`, `FakeGitService`; `workflows/platforms/work_tracking`: `FakeGhCli` |
| Unit tests | Component behavior and Workflow rules against fakes | `tests/unit`: agent builder, `dev`, `plan_implement`, platform git, and library tests |
| Architecture checks | Import rules enforced as a test | `tests/unit/test_library.py`: `lint-imports` |

## References

- [Loop Library Workflow Architecture](str-loop-library-workflow-architecture.md)
- [Autonomous Spec Delivery](dom-autonomous-spec-delivery.md)
- [Agent Client](str-agent-client.md)

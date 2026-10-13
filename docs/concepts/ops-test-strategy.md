# Test Strategy: Unit Tests Against Fakes

## Purpose

Loop's behavior is spread over the shipped library (agent builder, CLI runners, git, hooks, sessions) and user-owned Workflow scripts, and every real boundary (`git`, `gh`, the agent CLIs, Docker) is slow, stateful, or unavailable in CI. This concept fixes where the real/stand-in line sits and how a Workflow's behavior is proven end to end.

## Approach

One unit test group runs in the project's single test command (`pytest`). It tests one component or one Workflow rule at a time against fakes at the process boundaries; a Workflow scenario is driven through the Workflow's `main` over the same fakes, so the real builder, prompt rendering, response extraction, and Workflow logic cooperate.

- **Library seams:** `CliRunner` and `GitService` (and `DockerService`) are the replaceable boundaries; tests build `AgentBuilder(runner, git, docker)` over a `FakeRunner` and `FakeGitService`.
- **Workflow seams:** a Workflow's Git reads and publication (`GitService`) and its GitHub client (`FakeGhCli`) are injected as in-memory fakes; the `dev` Workflow takes `new_agent`, so tests supply a builder over the fake runner.
- **Ticket delegate seam:** reusable Ticket orchestration is tested through `deliver_tickets` with function delegates returning typed results; multi-repository scenarios drive `dev.main` with separate histories, clients, and delegates to prove target isolation and harness-only Ticket updates.
- **Workflow-local harness:** `workflows/dev/tests/doubles.py` holds dev's fakes, `DevHarness`, and response-envelope builders. `tests/workflow_harness.py` independently supports `plan_implement`; dev tests import no sibling harness.
- **Architecture checks:** import-linter contracts run as a test.

## Rules

- MUST test every shipped component against its own boundary fake, one behavior per test.
- MUST NOT start a real `git`, `gh`, `docker`, or agent CLI process, or reach the network, from any test; the project has no live or integration test group.
- MUST drive a Workflow scenario through the Workflow's `main` with injected dependencies, with the real builder, prompt rendering, and response extraction.
- MUST make the runner fake or injected Ticket delegate commit through the Git fake before reporting completion, so Git validation runs against fake state.
- MUST exercise agent response parsing with raw runner output; tests of the independent Ticket delegate seam may return typed results without parsing an agent envelope.
- MUST assert a scenario outcome on effects at the fake boundaries — tracker writes, pushes, CLI invocations and prompts — and MUST NOT assert on Workflow internals.
- MUST state each scenario as Given/when/then in its docstring, and name each test as a sentence of the behavior it proves.
- MUST keep fakes and Workflow wiring in the owning Workflow's test harness; a test module MUST NOT rebuild them.
- MUST run the architecture checks (library layering, Workflows import only the public `loop` API, dev imports no sibling helpers, Ticket orchestration imports no agent delegate) inside the test run.
- MUST keep test fakes out of the public `loop` API.

## Python compatibility

The library and example Workflows support Python 3.10+, including 3.10.12; [package metadata](../../pyproject.toml) declares the minimum. For runtime compatibility changes, validate in isolated environments on exact Python 3.10.12 and a newer supported interpreter (currently 3.12). Use each environment's `python` for both installation and execution:

```sh
python --version
python -m pip install -e ".[dev]"
python -m pip check
python -m pytest
python -m workflows.dev --help
python -m workflows.plan_implement --help
```

Run from the repository root with `git` installed. The root test command covers library tests, both example Workflows, and import-linter contracts. The `--help` checks exercise Workflow imports and argument parsing without starting agents or contacting GitHub.

Syntax checks alone do not prove runtime compatibility: imports, standard-library APIs, dependency resolution, and error paths must also work. Preserve hook-point string conversion, formatting, and JSON serialization across runtimes. Cleanup failures must not mask the original error: Python 3.11+ attaches exception notes; Python 3.10 logs the secondary failure with its traceback. Tests cover the applicable path on each runtime.

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
| Workflow wiring for tests | One local harness binds dev to fakes at every boundary | `workflows/dev/tests/doubles.py`: `DevHarness`, `FakeRunner`, `FakeGitService`, `FakeWorkflowGit` |
| Envelope builders | Response envelopes the Workflows extract | `tests/workflow_harness.py`: `plan_envelope`, `implement_envelope`; `workflows/dev/tests/doubles.py`: `envelope` |
| Boundary fakes | One fake per boundary | `workflows/dev/tests/doubles.py`: dev's runner, Git, and `FakeGhCli` |
| Unit tests | Library and Workflow rules against fakes | `tests/unit` and `workflows/dev/tests`; both discovered by root `pytest` |
| Package independence | Dev runs without sibling Workflow files and resolves its own prompts | `workflows/dev/tests/test_package.py` |
| Architecture checks | Import rules enforced as a test | `tests/unit/test_library.py`: `lint-imports` |

## References

- [Loop Library Workflow Architecture](str-loop-library-workflow-architecture.md)
- [Autonomous Spec Delivery](dom-autonomous-spec-delivery.md)
- [Agent Client](str-agent-client.md)

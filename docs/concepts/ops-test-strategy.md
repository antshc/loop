# Test Strategy: Unit Tests Against Fakes

## Purpose

Loop's behavior is spread over the shipped library (agent builder, CLI runners, git, hooks, sessions) and user-owned Workflow scripts, and every real boundary (`git`, `gh`, the agent CLIs, Docker) is slow, stateful, or unavailable in CI. This concept fixes where the real/stand-in line sits and how a Workflow's behavior is proven end to end.

## Approach

One unit test group runs in the project's single test command (`pytest`). It tests one component or one Workflow rule at a time against fakes at the process boundaries; a Workflow scenario is driven through the Workflow's `main` over the same fakes, so the real builder, prompt rendering, response extraction, and Workflow logic cooperate.

- **Library seams:** `CliRunner` and `GitService` (and `DockerService`) are the replaceable boundaries; tests build `AgentBuilder(runner, git, docker)` over a `FakeRunner` and `FakeGitService`.
- **Workflow seams:** a Workflow's Git reads and publication (`GitClient`, `WorktreeGit`), its GitHub clients (`FakeGhCli`) and its agent launcher are injected as in-memory or scripted fakes. `implement_spec` and `pr_babysit` take them as `Services` through `main(argv, *, services=None)`, a fake that subclasses a service bound to the service's type with `Services.bind`. Tests substitute fakes by passing `Services` (no container and no override providers). A single Stage is tested as a one-Stage Workflow over a `StageRig` with fakes.
- **Runtime and definition tests:** the runtime's contracts, routes, budgets, ledger and crash recovery are tested against real temporary repositories with `RecordingSpans`; every Workflow definition has a test that it builds and passes `check()`, and a golden of its `explain()` wiring table (`wiring.txt`) so a definition change is a reviewable diff. Structural tests under `workflows/tests/software_factory/architecture/` derive the remaining rules from the code.
- **Stage tests:** each Stage in `workflows/software_factory/stages/` is driven alone, as a one-Stage Workflow, with a scripted role agent over a real temporary repository and assertions on its produced Event, routes, ledger records and boundary effects, not its internals.
- **Ticket delegate seam:** reusable Ticket orchestration is tested through the Stage library against a real temporary repository with a scripted developer agent; implement_spec scenarios drive `main(argv, services=...)` to prove escalation, idle, hand-off, interruption, resume and release.
- **Workflow-local harness:** `workflows/tests/software_factory/workflows/implement_spec/harness.py` holds `ImplementSpecHarness` (a `StageRig` tracker and real temporary repository, a scripted `Agents` answering every role, a temporary ledger and `RecordingSpans`, with the process running in the rig's checkout so a read-only Stage never touches this repository); `workflows/tests/software_factory/workflows/pr_babysit/harness.py` does the same for babysit (`Babysit`: a pull request on the shared pull request fake over a real repository with a local bare remote, scripted address and maintainer agents, a pacer that closes the pull request, a configuration file and a temporary ledger). Both reuse the shared `gh`-boundary fake in `tests/shared/fakes/`. `workflows/tests/loop/workflow_harness.py` independently supports `plan_implement`; implement_spec tests import no sibling harness.
- **Architecture checks:** import-linter contracts run as a test (`tests/test_architecture.py`).
- **Test homes:** library tests live in `tests/`, grouped by capability (`runtime/`, `agents/`, `shared/`); Workflow tests live in `workflows/tests/loop/` and `workflows/tests/software_factory/`. Neither is installed with the library.

## Rules

- MUST test every shipped component against its own boundary fake, one behavior per test.
- MUST NOT start a real `gh`, `docker`, or agent CLI process, or reach the network, from any test. Worktree lifecycle and PR publication recovery tests may use isolated temporary Git repositories and local bare remotes; they MUST NOT access user repositories or network remotes.
- MUST drive a Workflow scenario through the Workflow's `main` with dependencies injected as `services`, with the real builder, prompt rendering, and response extraction.
- MUST make the runner fake or injected Ticket delegate commit through the Git fake before reporting completion, so Git validation runs against fake state.
- MUST exercise agent response parsing with raw runner output; tests of the independent Ticket delegate seam may return typed results without parsing an agent envelope.
- MUST assert a scenario outcome on effects at the fake boundaries — tracker writes, pushes, CLI invocations and prompts — and MUST NOT assert on Workflow internals.
- MUST state each scenario as Given/when/then in its docstring, and name each test as a sentence of the behavior it proves.
- MUST keep library tests under `tests/`, grouped by capability and importing no Workflow, and factory tests under `workflows/tests/software_factory/`, mirroring the factory packages (`stages/`, `flows/`, `agents/`, `events/`, `shared/`, `workflows/<workflow>/`) and their folders; example tests live in `workflows/tests/loop/`.
- MUST test each Stage alone with fakes at its service boundaries, and give each Workflow definition a build-and-`check()` test and an `explain()` golden.
- MUST keep fakes and Workflow wiring in the owning Workflow's test harness, and the `gh`-boundary fake for GitHub semantics in `tests/shared/fakes/`; Workflow harnesses reuse or extend it, and a test module MUST NOT rebuild them.
- MUST run the architecture checks (library layering, Workflows import only the public `loop` API, implement_spec imports no sibling helpers, Ticket orchestration imports no agent delegate) inside the test run.
- MUST keep test fakes out of the public `loop` API.

## Python compatibility

The library and example Workflows support Python 3.14+; [package metadata](../../pyproject.toml) declares the minimum. For runtime compatibility changes, validate in an isolated environment on Python 3.14 and on any newer supported interpreter. Use each environment's `python` for both installation and execution:

```sh
python --version
python -m pip install -e ".[dev]" --config-settings editable_mode=strict
python -m pip check
python -m pytest
python -m workflows.software_factory.implement_spec --help
python -m workflows.software_factory.pr_babysit --help
python -m workflows.software_factory.address_once --help
python -m workflows.loop.plan_implement --help
```

Run from the repository root with `git` installed. The `dev` extra includes no dependency-injection container. The root test command covers the library tests in `tests/` (runtime, typed agent, shared Git, process and GitHub helpers, import-linter contracts), the `plan_implement` example, the factory's Stages and the `implement_spec`, `pr_babysit` and `address_once` Workflows, and the derived architecture tests in `workflows/tests/software_factory/architecture/`. The `--help` checks exercise Workflow imports and argument parsing without starting agents or contacting GitHub.

Syntax checks alone do not prove runtime compatibility: imports, standard-library APIs, dependency resolution, and error paths must also work. Hook-point enums are `StrEnum`s, so string conversion, formatting, and JSON serialization yield the value. Cleanup failures must not mask the original error: the secondary failure is attached to it as an exception note.

## Example

A `implement_spec` scenario drives `main` through `ImplementSpecHarness`: the runner fake commits through the workflow Git fake and answers with the raw response envelope, and the test asserts on tracker writes and publication.

```python
harness = ImplementSpecHarness(tmp_path)
harness.runner.handler = harness.commit_then(lambda sha: envelope("Checkout|10", result={**COMPLETED, "commit": sha}))
# run implement_spec.main over the harness, then assert on harness.writes() and harness.git.pushed_count
```

## Implementation Map

Verify — may drift.

| Concern | Stable anchor | Semantic locator |
|---|---|---|
| Workflow wiring for tests | One local harness binds each Workflow to fakes at every boundary | `workflows/tests/software_factory/workflows/implement_spec/harness.py`: `ImplementSpecHarness`, `Agents`; `workflows/tests/software_factory/workflows/pr_babysit/harness.py`: `Babysit`, `Agents` |
| Envelope builders | Response envelopes the Workflows extract | `workflows/tests/loop/workflow_harness.py`: `plan_envelope`, `implement_envelope`; `workflows/tests/software_factory/stages/support.py`: `envelope`, `answers` |
| Boundary fakes | One fake per boundary | `workflows/tests/software_factory/workflows/implement_spec/harness.py`: `Agents` (the scripted agent runner) over a real temporary repository; `tests/shared/fakes/gh.py`: `FakeGhCli` and `ScriptedGh` for `gh`, with the pull request side (`FakePullRequestHost`) in `tests/shared/fakes/pull_requests.py`, shared by the `implement_spec` and `pr_babysit` harnesses |
| Compatibility goldens | Values a migration must not change are captured from the old code first, then compared byte for byte | `workflows/tests/software_factory/workflows/pr_babysit/golden.json` with `golden.py` and `test_golden.py`: babysit fingerprints and agent envelopes (its saved per-PR states seed `test_legacy.py`; the state golden is retired with the state model); `workflows/tests/software_factory/agents/goldens/prompts/`: the role prompts; `workflows/tests/software_factory/workflows/implement_spec/fake_run_tracker_writes.json`: the fake run's tracker writes; `wiring.txt`: the definition's wiring table |
| Unit tests | Library, runtime, Stage and Workflow rules against fakes | `tests/` (`runtime`, `agents`, `shared`) and `workflows/tests/` (`loop`, and `software_factory` with `stages`, `flows`, `agents`, `events`, `shared`, `workflows/{implement_spec,pr_babysit,address_once}`, each mirroring its source folders); all discovered by root `pytest` |
| Stage tests | One Stage run alone as a one-Stage Workflow | `workflows/tests/software_factory/stages/support.py`: `StageRig`, `DeveloperScript`; `workflows/tests/software_factory/stages/test_*.py` |
| Definition check and wiring golden | Every definition builds, passes `check()`, and its `explain()` equals the stored table | `workflows/tests/software_factory/workflows/<workflow>/test_definition.py`, `wiring.txt`; `workflows/tests/software_factory/architecture/` |
| Package independence | `implement_spec` runs with only `workflows/software_factory/agents`, `workflows/software_factory/stages`, `src/runtime`, `workflows/software_factory/shared` and no sibling Workflow files, and the role agents resolve their prompts from their own packages | `lint-imports` contracts "No Workflow imports another" and "The factory is layered" in `pyproject.toml`; `workflows/tests/software_factory/architecture/test_portability.py` |
| Architecture checks | Import rules enforced as a test | `tests/test_architecture.py`: every import-linter contract in `pyproject.toml` |

## References

- [Loop Library Workflow Architecture](str-loop-library-workflow-architecture.md)
- [Autonomous Spec Delivery](dom-autonomous-spec-delivery.md)
- [Agent Client](str-agent-client.md)

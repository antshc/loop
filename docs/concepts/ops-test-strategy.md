# Test Strategy: Unit and Functional Slice Tests

## Purpose

Loop's behavior is spread over shipped components (agent client, output parser, Sandboxes, Git and GitHub clients, stores) and user-owned Workflow scripts, and every real boundary (`git`, `gh`, the Copilot CLI, Docker) is slow, stateful, or unavailable in CI. This concept fixes what each test group covers, where the real/stand-in line sits, and how a Workflow's behavior is proven end to end.

## Approach

Tests form two groups that run independently of each other and together in one test run.

- **Unit group** — one component or one Workflow rule at a time against its process-boundary stand-ins. It is fast and covers edge cases, plus the architecture checks. Workflow rules that do not depend on a real Loop part (retry bounds, labelling, worktree location, argument handling) are tested here with the agent client replaced by `FakeAgentClient`.
- **Functional slice group** (the integration group) — one business scenario of a Workflow at a time, driven through the Workflow's entry point with the shipped Copilot agent client, its output parser, the prompt preprocessor, and a host Sandbox all real. Only Git, GitHub, the Copilot CLI process, and the execution store are stand-ins, so a slice proves that the real parts cooperate. A scenario earns a slice test when its outcome depends on those parts interacting — response extraction, prompt content, live output, Git validation, publication — not when a unit test already settles it.

The shared Workflow wiring (`DevHarness`) and the provider-shaped output builders live in one support module imported by both groups; the doubles come from `loop.testing`.

## Rules

- MUST keep unit and functional slice tests in separate groups that can each be selected alone and that both run in the project's single test command.
- MUST test every shipped component in the unit group against its own process-boundary double, one behavior per test.
- MUST NOT start a real `gh`, Copilot CLI, or Docker process, or reach the network, from any test; real Git MAY run only in throwaway repositories under the test temp folder.
- MUST test each Workflow rule in the unit group and each Workflow business scenario that depends on real Loop parts interacting in the functional slice group.
- MUST drive a functional slice test through the Workflow's entry point with injected dependencies, with the real Copilot agent client, output parser, prompt preprocessor, and host Sandbox, and stand-ins only for Git, GitHub, the Copilot CLI process, and the execution store.
- MUST feed the Copilot CLI stand-in a provider-shaped event stream (unrelated events, response split across delta events, closing events); a slice test MUST NOT hand the Workflow a pre-built agent result.
- MUST make the agent stand-in commit through the Git stand-in before it reports completion, so Git validation runs against real stand-in state.
- MUST assert a slice outcome on effects at the stand-in boundaries — tracker writes, pushes, Copilot CLI invocations and prompts, live output lines — and MUST NOT assert on Workflow internals.
- MUST state each slice scenario as Given/when/then in its docstring, and name each test as a sentence of the behavior it proves.
- MUST keep Workflow wiring and event-stream builders in the shared support module; a test module MUST NOT rebuild them.
- MUST run the architecture checks (import-linter contracts, public API exposes no double, Workflows import only the public API) inside the unit group.
- MUST keep test doubles in `loop.testing`, never in the public `loop` API.

## Example

A slice scenario with two actionable Tickets (trimmed from the real test): the agent stand-in commits via the Git stand-in, then answers with a provider-shaped stream for whichever task id its prompt carries.

```python
def handler(prompt: str) -> list[str]:
    identifier = _TASK_ID.search(prompt)[1]
    commit = harness.git.commit(_only_worktree(harness.git), f"ccode({identifier}): work")
    return copilot_event_frames(identifier, "completed", {"commit": commit, "summary": "done", "verification": "ran tests"})

code, recorder = _run_with_real_agent(harness, FakeCopilotCli(handler))

assert code == 0
assert [call[2] for call in closes] == ["10", "11"]   # each Ticket closed once, in order
assert len(harness.git.pushed) == 1                   # one publish for the Spec
```

## Implementation Map

| Concern | Stable anchor | Semantic locator |
|---|---|---|
| Workflow wiring for tests | One harness binds the Workflow entry point to stand-ins at every process boundary | `loop`: `DevHarness`, `RecordingExecutor` |
| Provider-shaped output | Event streams modeled on the recorded Copilot CLI output experiment | `loop`: `copilot_event_frames`, `FakeCopilotCli` |
| Process-boundary doubles | One public double per process boundary | `loop`: `FakeGit`, `FakeDocker`, `FakeCopilotCli`, `FakeAgentClient`, `InMemoryExecutionStore`; `workflows/platforms/work_tracking`: `FakeGhCli` |
| Unit group | Component behavior and Workflow rules against doubles | `loop`: Workflow, agent, Sandbox, Git client, GitHub client, store, process, and preprocessor unit tests |
| Functional slice group | Workflow scenarios through the real agent client, parser, preprocessor, and host Sandbox | `loop`: dev Workflow happy path, response extraction, Git validation and escalation, publication when branch ahead |
| Architecture checks | Import rules and public-API surface enforced as tests | `loop`: import-linter contracts, public API exposes no fake, Workflows import only the public API |

## References

- [Loop Library Workflow Architecture](str-loop-library-workflow-architecture.md)
- [Autonomous Spec Delivery](dom-autonomous-spec-delivery.md)
- [Agent Client](str-agent-client.md)
- [Copilot CLI JSON output events](../experiments/copilot-cli-json-output-events.md)

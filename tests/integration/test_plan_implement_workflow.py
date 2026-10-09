"""Integration test: `plan_implement.main` through the real Copilot agent client, parser, and prompt preprocessor.

Only the Copilot CLI process and Git are stand-ins (docs/concepts/str-loop-library-workflow-architecture.md).
"""

from __future__ import annotations

from pathlib import Path

from loop import AgentBinding, CopilotClient, InMemorySessionStore, PromptPreprocessor
from loop.testing import FakeCopilotCli
from workflows import plan_implement

from workflow_harness import PlanImplementHarness, RecordingExecutor, copilot_event_frames


def _real_agent_factory(executor):
    def factory(binding: AgentBinding) -> CopilotClient:
        return CopilotClient(executor, PromptPreprocessor(lambda command: ""), InMemorySessionStore(), workspace=binding.workspace)

    return factory


def test_the_implementing_prompt_carries_the_plan_and_each_run_has_its_own_model_and_effort_flags(
    tmp_path: Path,
) -> None:
    """Given a task, when the Workflow runs, then the second CLI invocation's prompt contains the plan from the
    first reply, each invocation carries its own model and reasoning-effort flags, and the exit code is 0."""
    harness = PlanImplementHarness(tmp_path)
    responses = iter(
        [
            copilot_event_frames("plan", "completed", {"plan": "Add the widget behind a feature flag."}),
            copilot_event_frames("implement", "completed", {}),
        ]
    )
    fake_cli = FakeCopilotCli(lambda prompt: next(responses))
    recorder = RecordingExecutor(fake_cli)

    code = harness.run(agent_factory=_real_agent_factory(recorder))

    assert code == 0
    assert len(fake_cli.calls) == 2
    plan_call, implement_call = fake_cli.calls
    assert plan_call[plan_call.index("--model") + 1] == plan_implement.PLAN_MODEL
    assert plan_call[plan_call.index("--reasoning-effort") + 1] == plan_implement.PLAN_EFFORT
    assert implement_call[implement_call.index("--model") + 1] == plan_implement.IMPLEMENT_MODEL
    assert implement_call[implement_call.index("--reasoning-effort") + 1] == plan_implement.IMPLEMENT_EFFORT
    assert "Add the widget behind a feature flag." in implement_call[implement_call.index("-p") + 1]

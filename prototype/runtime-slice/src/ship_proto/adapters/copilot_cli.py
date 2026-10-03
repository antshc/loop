from __future__ import annotations

from ship_proto.runtime.context import RunContext
from ship_proto.runtime.contracts.agent_client import AgentClient, AgentRunResult


class DummyCopilotCli(AgentClient):
    """Stands in for the Copilot CLI; echoes the prompt instead of invoking an agent."""

    def __init__(self, ctx: RunContext, *, fail_if_contains: str | None = None) -> None:
        self._ctx = ctx
        self._fail_if_contains = fail_if_contains
        self.prompts: list[str] = []

    def run(self, prompt: str) -> AgentRunResult:
        self.prompts.append(prompt)
        if self._ctx.dry_run:
            return AgentRunResult(success=True, output="[dry-run] agent not invoked")
        if self._fail_if_contains and self._fail_if_contains in prompt:
            return AgentRunResult(success=False, output="dummy agent failure")
        return AgentRunResult(success=True, output=f"dummy agent handled: {prompt[:60]}")

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from orb.contracts.agent_client import AgentClient, AgentResult

Handler = Callable[[str, Path], str | AgentResult]


class ScriptedAgent(AgentClient):
    """Delegates each run to a handler; for testing and dry-running workflows without an LLM."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler
        self.prompts: list[str] = []

    def run(self, prompt: str, cwd: Path) -> AgentResult:
        self.prompts.append(prompt)
        outcome = self._handler(prompt, cwd)
        return outcome if isinstance(outcome, AgentResult) else AgentResult(success=True, output=outcome)

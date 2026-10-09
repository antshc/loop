from __future__ import annotations

from collections.abc import Callable

from loop.agents.copilot import CopilotOutputParser
from loop.contracts.agent_client import AgentClient, AgentOptions, AgentResult, AgentSession
from loop.process import CommandResult
from loop.prompt import PromptPreprocessor
from loop.stores.memory import InMemorySessionStore

Handler = Callable[[str, AgentOptions], "str | AgentResult"]


class FakeAgentClient(AgentClient):
    """Keeps rendering and session handling; a handler stands in for the provider CLI."""

    def __init__(self, handler: Handler | None = None) -> None:
        super().__init__(
            lambda command, *, timeout_s=None, on_line=None: CommandResult(0, "", ""),
            PromptPreprocessor(lambda command: ""),
            InMemorySessionStore(),
        )
        self._handler = handler or (lambda prompt, options: "")
        self.calls: list[tuple[str, str | None, str | None, AgentOptions]] = []

    def _invoke(
        self,
        prompt: str,
        model: str | None,
        reasoning_effort: str | None,
        options: AgentOptions,
        session: AgentSession | None,
        *,
        resume: bool,
    ) -> AgentResult:
        self.calls.append((prompt, model, reasoning_effort, options))
        outcome = self._handler(prompt, options)
        return outcome if isinstance(outcome, AgentResult) else CopilotOutputParser().parse(outcome, "", 0)

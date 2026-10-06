from __future__ import annotations

from collections.abc import Callable

from orb.contracts.agent_client import AgentClient, AgentOptions, AgentResult, AgentSession
from orb.process import CommandResult
from orb.prompt import PromptPreprocessor
from orb.stores.memory import InMemorySessionStore

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
        self.calls: list[tuple[str, AgentOptions]] = []

    def _invoke(
        self,
        prompt: str,
        options: AgentOptions,
        session: AgentSession | None,
        *,
        resume: bool,
    ) -> AgentResult:
        self.calls.append((prompt, options))
        outcome = self._handler(prompt, options)
        return outcome if isinstance(outcome, AgentResult) else AgentResult(outcome, "", 0)

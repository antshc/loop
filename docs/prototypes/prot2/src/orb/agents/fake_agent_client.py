from __future__ import annotations

from collections.abc import Callable, Mapping

from orb.contracts.agent_client import AgentClient, AgentOptions, AgentResult

Handler = Callable[[str, Mapping[str, str], AgentOptions], "str | AgentResult"]


class FakeAgentClient(AgentClient):
    """Skips rendering and the CLI: a handler stands in for the agent and every call is recorded."""

    def __init__(self, handler: Handler | None = None) -> None:
        self._handler = handler or (lambda prompt, prompt_args, options: "")
        self.calls: list[tuple[str, Mapping[str, str], AgentOptions]] = []

    def run(
        self,
        prompt: str,
        prompt_args: Mapping[str, str] | None = None,
        options: AgentOptions | None = None,
    ) -> AgentResult:
        args = prompt_args or {}
        options = options or AgentOptions()
        self.calls.append((prompt, args, options))
        outcome = self._handler(prompt, args, options)
        return outcome if isinstance(outcome, AgentResult) else AgentResult(outcome, "", 0)

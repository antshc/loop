from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from orb.contracts.agent_client import AgentOptions, AgentResult
from orb.contracts.capsule import AgentClientFactory, Capsule, CapsuleBinding
from orb.process import CommandExecutor, CommandResult, OnLine, checked_output, execute


class NoCapsule(Capsule):
    """No sandbox: the agent runs directly in the workspace with the user's own permissions."""

    def __init__(
        self,
        workspace: Path | str,
        *,
        executor: CommandExecutor | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self._executor = executor or self._host_executor

    @property
    def workspace(self) -> str:
        return str(self._workspace)

    @property
    def isolated(self) -> bool:
        return False

    @property
    def executor(self) -> CommandExecutor:
        return self._executor

    def run(
        self,
        agent: AgentClientFactory,
        prompt: str,
        prompt_args: Mapping[str, str] | None = None,
        options: AgentOptions | None = None,
    ) -> AgentResult:
        binding = CapsuleBinding(self._executor, self.isolated, self.workspace)
        return agent(binding).run(prompt, prompt_args, options)

    def exec(self, command: str, *, timeout_s: float | None = None) -> str:
        return checked_output(command, self._executor(command, timeout_s=timeout_s))

    def close(self) -> None:
        pass

    def _host_executor(
        self, command: Sequence[str] | str, *, timeout_s: float | None = None, on_line: OnLine | None = None
    ) -> CommandResult:
        return execute(command, cwd=self._workspace, timeout_s=timeout_s, on_line=on_line)

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

from orb.contracts.agent_client import AgentOptions, AgentResult
from orb.contracts.capsule import AgentClientFactory, Capsule, CapsuleBinding
from orb.errors import Cancelled
from orb.process import CommandExecutor, CommandResult, OnLine, checked_output, execute


class NoCapsule(Capsule):
    """No sandbox: the agent runs directly in the workspace with the user's own permissions."""

    def __init__(
        self,
        workspace: Path | str,
        *,
        executor: CommandExecutor | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        self._workspace = Path(workspace)
        self._executor = executor or self._host_executor
        self._cancel = cancel

    @property
    def workspace(self) -> str:
        return str(self._workspace)

    @property
    def isolated(self) -> bool:
        return False

    @property
    def executor(self) -> CommandExecutor:
        return self._bound_executor

    def run(
        self,
        agent: AgentClientFactory,
        prompt: str,
        prompt_args: Mapping[str, str] | None = None,
        options: AgentOptions | None = None,
    ) -> AgentResult:
        binding = CapsuleBinding(self._bound_executor, self.isolated, self.workspace)
        result = agent(binding).run(prompt, prompt_args, options)
        if self._cancel is not None and self._cancel.is_set():
            self.close()
            raise Cancelled()
        return result

    def exec(self, command: str, *, timeout_s: float | None = None) -> str:
        return checked_output(command, self._bound_executor(command, timeout_s=timeout_s))

    def close(self) -> None:
        pass

    def _bound_executor(
        self, command: Sequence[str] | str, *, timeout_s: float | None = None, on_line: OnLine | None = None
    ) -> CommandResult:
        return self._executor(command, timeout_s=timeout_s, on_line=on_line, cancel=self._cancel)

    def _host_executor(
        self,
        command: Sequence[str] | str,
        *,
        timeout_s: float | None = None,
        on_line: OnLine | None = None,
        cancel: threading.Event | None = None,
    ) -> CommandResult:
        return execute(command, cwd=self._workspace, timeout_s=timeout_s, on_line=on_line, cancel=cancel)

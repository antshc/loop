from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from types import TracebackType
from typing import Protocol

from orb.contracts.agent_client import AgentClient, AgentOptions, AgentResult
from orb.process import CommandResult


class CommandExecutor(Protocol):
    """Runs a command in the capsule's environment; a non-zero exit is returned, not raised."""

    def __call__(
        self, command: Sequence[str] | str, *, timeout_s: float | None = None
    ) -> CommandResult: ...


AgentClientFactory = Callable[[CommandExecutor], AgentClient]


class Capsule(ABC):
    """The environment an agent runs in; wraps an AgentClient bound to that environment."""

    @property
    @abstractmethod
    def workspace(self) -> str: ...

    @abstractmethod
    def run(
        self,
        prompt: str,
        prompt_args: Mapping[str, str] | None = None,
        options: AgentOptions | None = None,
    ) -> AgentResult: ...

    @abstractmethod
    def exec(self, command: str, *, timeout_s: float | None = None) -> str:
        """Run a shell command inside the capsule; raise CommandError on failure."""

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> Capsule:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

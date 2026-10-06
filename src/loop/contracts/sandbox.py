from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import TracebackType

from loop.contracts.agent_client import AgentClient, AgentOptions, AgentResult
from loop.process import CommandExecutor


@dataclass(frozen=True)
class SandboxBinding:
    """What a Sandbox gives the agent factory on each run: its executor, isolation, and workspace."""

    executor: CommandExecutor
    isolated: bool
    workspace: str


AgentClientFactory = Callable[[SandboxBinding], AgentClient]


class Sandbox(ABC):
    """The environment an agent runs in: workspace, exec, close, and the executor an agent runs through."""

    @property
    @abstractmethod
    def workspace(self) -> str:
        """The harness root, as an absolute path identical inside and outside the Sandbox."""

    @property
    @abstractmethod
    def isolated(self) -> bool:
        """Whether this Sandbox isolates the agent from the host."""

    @property
    @abstractmethod
    def executor(self) -> CommandExecutor:
        """The executor an agent runs through."""

    @abstractmethod
    def run(
        self,
        agent: AgentClientFactory,
        prompt: str,
        prompt_args: Mapping[str, str] | None = None,
        options: AgentOptions | None = None,
    ) -> AgentResult: ...

    @abstractmethod
    def exec(self, command: str, *, timeout_s: float | None = None) -> str:
        """Run a shell command inside the sandbox; raise CommandError on failure."""

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

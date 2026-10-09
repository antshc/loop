from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loop.process import CommandExecutor
from loop.prompt import Prompt


@dataclass(frozen=True)
class AgentOptions:
    """Per-run agent configuration."""

    agent: str | None = None
    session_key: str | None = None
    session_name_prefix: str = ""
    timeout_s: float | None = None
    add_dirs: tuple[Path, ...] = ()
    deny_tools: tuple[str, ...] = ()
    extra_args: tuple[str, ...] = ("--no-color",)


@dataclass(frozen=True)
class AgentSession:
    key: str
    name: str


@dataclass(frozen=True)
class AgentResult:
    stdout: str
    stderr: str
    exit_code: int
    response: str = ""
    success: bool = False

    @property
    def output(self) -> str:
        return self.stdout if self.success else self.stdout + self.stderr


class AgentOutputParser(ABC):
    """Extracts the agent's response envelope from captured process output and decides success, after exit."""

    @abstractmethod
    def parse(self, stdout: str, stderr: str, exit_code: int) -> AgentResult: ...


class SessionStore(ABC):
    """Maps a logical session key to the provider session name; the provider owns the transcript."""

    @abstractmethod
    def get(self, key: str) -> AgentSession | None: ...

    @abstractmethod
    def save(self, session: AgentSession) -> None: ...


@dataclass(frozen=True)
class AgentBinding:
    """What a run gives the agent factory: the executor the agent runs through and the harness-root workspace."""

    executor: CommandExecutor
    workspace: str


class AgentClient(ABC):
    """Renders the prompt, resolves the logical session, and leaves command building to the provider."""

    def __init__(
        self,
        executor: CommandExecutor,
        execute_command: Callable[[str], str],
        sessions: SessionStore,
    ) -> None:
        self._executor = executor
        self._execute_command = execute_command
        self._sessions = sessions

    def run(
        self,
        prompt: Prompt,
        model: str | None = None,
        reasoning_effort: str | None = None,
        options: AgentOptions | None = None,
    ) -> AgentResult:
        options = options or AgentOptions()
        text = prompt.render(self._execute_command)
        if options.session_key is None:
            return self._invoke(text, model, reasoning_effort, options, None, resume=False)

        session = self._sessions.get(options.session_key)
        if session is not None:
            return self._invoke(text, model, reasoning_effort, options, session, resume=True)

        session = AgentSession(
            key=options.session_key,
            name=f"{options.session_name_prefix}{options.session_key}",
        )
        result = self._invoke(text, model, reasoning_effort, options, session, resume=False)
        # A failed create may leave no provider session to resume.
        if result.success:
            self._sessions.save(session)
        return result

    def exit(self) -> None:
        """Releases what the client holds; the default holds nothing."""

    @abstractmethod
    def _invoke(
        self,
        prompt: str,
        model: str | None,
        reasoning_effort: str | None,
        options: AgentOptions,
        session: AgentSession | None,
        *,
        resume: bool,
    ) -> AgentResult: ...


AgentClientFactory = Callable[[AgentBinding], AgentClient]

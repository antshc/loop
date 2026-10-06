from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from orb.process import CommandExecutor
from orb.prompt import PromptPreprocessor

DEFAULT_COMPLETION_SIGNAL = "<promise>COMPLETE</promise>"


@dataclass(frozen=True)
class AgentOptions:
    """Per-run agent configuration."""

    model: str | None = None
    agent: str | None = None
    session_key: str | None = None
    session_name_prefix: str = ""
    timeout_s: float | None = None
    add_dirs: tuple[Path, ...] = ()
    extra_args: tuple[str, ...] = ("--allow-all-tools", "--no-color")


@dataclass(frozen=True)
class AgentSession:
    key: str
    name: str


@dataclass(frozen=True)
class AgentResult:
    stdout: str
    stderr: str
    exit_code: int
    completed: bool = False

    @property
    def success(self) -> bool:
        return self.exit_code == 0 or self.completed

    @property
    def output(self) -> str:
        return self.stdout if self.success else self.stdout + self.stderr


class SessionStore(ABC):
    """Maps a logical session key to the provider session name; the provider owns the transcript."""

    @abstractmethod
    def get(self, key: str) -> AgentSession | None: ...

    @abstractmethod
    def save(self, session: AgentSession) -> None: ...


class AgentClient(ABC):
    """Renders the prompt, resolves the logical session, and leaves command building to the provider."""

    def __init__(
        self,
        executor: CommandExecutor,
        preprocessor: PromptPreprocessor,
        sessions: SessionStore,
    ) -> None:
        self._executor = executor
        self._preprocessor = preprocessor
        self._sessions = sessions

    def run(
        self,
        prompt: str,
        prompt_args: Mapping[str, str] | None = None,
        options: AgentOptions | None = None,
    ) -> AgentResult:
        options = options or AgentOptions()
        text = self._preprocessor.process(prompt, prompt_args)
        if options.session_key is None:
            return self._invoke(text, options, None, resume=False)

        session = self._sessions.get(options.session_key)
        if session is not None:
            return self._invoke(text, options, session, resume=True)

        session = AgentSession(
            key=options.session_key,
            name=f"{options.session_name_prefix}{options.session_key}",
        )
        result = self._invoke(text, options, session, resume=False)
        # A failed create may leave no provider session to resume.
        if result.success:
            self._sessions.save(session)
        return result

    @abstractmethod
    def _invoke(
        self,
        prompt: str,
        options: AgentOptions,
        session: AgentSession | None,
        *,
        resume: bool,
    ) -> AgentResult: ...

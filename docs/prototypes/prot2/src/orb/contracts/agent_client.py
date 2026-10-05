from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_COMPLETION_SIGNAL = "<promise>COMPLETE</promise>"


@dataclass(frozen=True)
class AgentOptions:
    """Per-run agent configuration."""

    model: str | None = None
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

    @property
    def success(self) -> bool:
        return self.exit_code == 0

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
    @abstractmethod
    def run(
        self,
        prompt: str,
        prompt_args: Mapping[str, str] | None = None,
        options: AgentOptions | None = None,
    ) -> AgentResult:
        """Render `prompt` with `prompt_args`, run it once, and return the captured result."""

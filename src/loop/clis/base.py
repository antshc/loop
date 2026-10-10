from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

# Imported for annotations only: AgentProfile and AgentCli refer to each other and to RunContext.
if TYPE_CHECKING:
    from ..hooks import AgentCliHook, AgentCliHookPoint, AgentCliHookWiring
    from ..run import AgentRequest, RunContext
    from ..sessions import CliOutcome, NativeHandle, SessionName, Turn


class AgentCli(Protocol):
    """Adapter for one agent CLI; the library runs the process, sessions, dry-run and Docker."""

    # Unique per CLI: it scopes session keys.
    name: str
    hook_points: frozenset[AgentCliHookPoint]

    def hook_wiring(self, hooks: tuple[AgentCliHook, ...], turn: Turn, workdir: Path) -> AgentCliHookWiring:
        """Pure translation of `hooks` into this CLI's native wiring; does no I/O."""
        ...

    def native_point(self, point: AgentCliHookPoint) -> str:
        """The CLI's own name for `point`."""
        ...

    def handle_for_new(self, name: SessionName) -> NativeHandle | None:
        """The handle the CLI uses for a session started under `name`, or None when the CLI assigns its own."""
        ...

    def command(
        self, request: AgentRequest, profile: AgentProfile, turn: Turn, context: RunContext
    ) -> list[str]: ...

    def parse(self, stdout: str, turn: Turn, exit_code: int) -> CliOutcome:
        """Raises SessionHandleMissing when no handle can be determined."""
        ...


@dataclass(frozen=True)
class AgentProfile:
    """One CLI plus its settings; None adds no flag, so the CLI default applies."""

    cli: AgentCli
    model: str | None = None
    reasoning_effort: str | None = None
    context: str | None = None
    args: tuple[str, ...] = ()

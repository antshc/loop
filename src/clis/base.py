from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ..run import AgentContext
from ..run.instructions import Instructions
from ..run.settings import freeze_env, validate_timeout
from .permissions import Permissions, UnsupportedPermissions, has_permission_args

# Imported for annotations only: AgentProfile and AgentCli refer to each other and to RunContext.
if TYPE_CHECKING:
    from ..hooks import AgentCliHook, AgentCliHookPoint, AgentCliHookWiring
    from ..run import AgentRequest, RunContext
    from ..sessions import CliOutcome, NativeHandle, SessionName, Turn
    from .transport import Transport


class AgentCli(Protocol):
    """Adapter for one agent CLI; the library runs the process, sessions, dry-run and Docker."""

    # Unique per CLI: it scopes session keys.
    name: str
    hook_points: frozenset[AgentCliHookPoint]
    transport: Transport

    def hook_wiring(
        self, hooks: tuple[AgentCliHook, ...], turn: Turn, workdir: Path
    ) -> AgentCliHookWiring:
        """Pure translation of `hooks` into this CLI's native wiring; does no I/O."""
        ...

    def native_point(self, point: AgentCliHookPoint) -> str:
        """The CLI's own name for `point`."""
        ...

    def handle_for_new(self, name: SessionName) -> NativeHandle | None:
        """The handle the CLI uses for a session started under `name`, or None when the CLI assigns its own."""
        ...

    def command(
        self,
        request: AgentRequest,
        profile: AgentProfile,
        turn: Turn,
        context: RunContext,
    ) -> list[str]: ...

    def parse(self, stdout: str, turn: Turn, exit_code: int) -> CliOutcome:
        """Raises SessionHandleMissing when no handle can be determined."""
        ...


@dataclass(frozen=True)
class AgentProfile:
    """Reusable agent instructions, CLI settings, and optional execution defaults."""

    cli: AgentCli
    model: str | None = None
    reasoning_effort: str | None = None
    context: str | None = None
    args: tuple[str, ...] = ()
    # Names the agent in observations, so a backend can tell several agents apart.
    name: str | None = None
    instructions: str | Instructions | None = field(default=None, kw_only=True)
    cwd: Path | None = field(default=None, kw_only=True)
    add_dirs: tuple[Path, ...] | None = field(default=None, kw_only=True)
    permissions: Permissions | None = field(default=None, kw_only=True)
    env: Mapping[str, str] | None = field(default=None, kw_only=True)
    timeout_sec: float | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        validate_timeout(self.timeout_sec)
        object.__setattr__(self, "env", freeze_env(self.env))
        if self.cwd is not None and not self.cwd.is_absolute():
            raise ValueError("profile cwd must be absolute")
        if self.add_dirs is not None:
            if any(not path.is_absolute() for path in self.add_dirs):
                raise ValueError("profile add_dirs must be absolute")
            object.__setattr__(self, "add_dirs", tuple(self.add_dirs))

    def apply_context(
        self, defaults: AgentContext, *, workspace: bool = False
    ) -> AgentContext:
        """Overlay configured execution defaults, preserving an already opened workspace."""
        values = {}
        for key in ("add_dirs", "env", "timeout_sec"):
            value = getattr(self, key)
            if value is not None:
                values[key] = value
        if self.cwd is not None and not workspace:
            values["cwd"] = self.cwd
        return replace(defaults, **values)

    def validate_permissions(self) -> None:
        """Fail before session registration if the CLI cannot enforce the policy."""
        if self.permissions is None:
            return
        if has_permission_args(self.args):
            raise ValueError("permission arguments conflict with profile permissions")
        translate = getattr(self.cli, "permission_args", None)
        if translate is None:
            raise UnsupportedPermissions(
                f"{self.cli.name} does not support tool permissions"
            )
        translate(self.permissions)

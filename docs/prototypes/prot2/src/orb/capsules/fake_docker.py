from __future__ import annotations

from collections.abc import Sequence

from orb.contracts.capsule import CommandExecutor
from orb.process import CommandResult


class FakeDocker:
    """Stands in for the `docker` binary; `exec` is forwarded to `inner`, standing in for the container."""

    def __init__(self, inner: CommandExecutor, *, image_user: str = "") -> None:
        self._inner = inner
        self._image_user = image_user
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self, command: Sequence[str] | str, *, timeout_s: float | None = None
    ) -> CommandResult:
        args = tuple(command)
        self.calls.append(args)
        if args[1:3] == ("image", "inspect"):
            return CommandResult(0, f"{self._image_user}\n", "")
        if args[1] == "exec":
            # docker exec -w <dir> <container> <command...>
            inner = args[5:]
            forwarded = inner[2] if inner[:2] == ("sh", "-c") else inner
            return self._inner(forwarded, timeout_s=timeout_s)
        return CommandResult(0, "", "")

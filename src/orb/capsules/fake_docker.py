from __future__ import annotations

from collections.abc import Sequence

from orb.process import CommandExecutor, CommandResult, OnLine


class FakeDocker:
    """Stands in for the `docker` binary; `exec` is forwarded to `inner`, standing in for the container."""

    def __init__(
        self,
        inner: CommandExecutor,
        *,
        image_user: str = "",
        run_results: Sequence[int | Exception] = (),
    ) -> None:
        self._inner = inner
        self._image_user = image_user
        self._run_results = list(run_results)
        self.calls: list[tuple[str, ...]] = []
        self.run_timeouts: list[float | None] = []

    def __call__(
        self,
        command: Sequence[str] | str,
        *,
        timeout_s: float | None = None,
        on_line: OnLine | None = None,
    ) -> CommandResult:
        args = tuple(command)
        self.calls.append(args)
        if args[1:3] == ("image", "inspect"):
            return CommandResult(0, f"{self._image_user}\n", "")
        if args[1:3] == ("run", "-d"):
            self.run_timeouts.append(timeout_s)
            if self._run_results:
                outcome = self._run_results.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return CommandResult(outcome, "", "" if outcome == 0 else f"exit {outcome}")
            return CommandResult(0, "", "")
        if args[1] == "exec":
            # docker exec -w <dir> <container> <command...>
            inner = args[5:]
            forwarded = inner[2] if inner[:2] == ("sh", "-c") else inner
            return self._inner(forwarded, timeout_s=timeout_s, on_line=on_line)
        return CommandResult(0, "", "")

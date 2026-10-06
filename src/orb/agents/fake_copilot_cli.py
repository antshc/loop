from __future__ import annotations

from collections.abc import Callable, Sequence

from orb.process import CommandResult

Handler = Callable[[str], "str | CommandResult"]
Shell = Callable[[str], str]


class FakeCopilotCli:
    """Stands in for the `copilot` binary behind a CommandExecutor; records argv and any shell commands."""

    def __init__(self, handler: Handler | None = None, shell: Shell | None = None) -> None:
        self._handler = handler or (lambda prompt: "")
        self._shell = shell or (lambda command: "")
        self.calls: list[tuple[str, ...]] = []
        self.shell_commands: list[str] = []

    def __call__(
        self, command: Sequence[str] | str, *, timeout_s: float | None = None
    ) -> CommandResult:
        if isinstance(command, str):
            self.shell_commands.append(command)
            return CommandResult(0, self._shell(command), "")
        argv = tuple(command)
        self.calls.append(argv)
        outcome = self._handler(argv[argv.index("-p") + 1])
        return outcome if isinstance(outcome, CommandResult) else CommandResult(0, outcome, "")

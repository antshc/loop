from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from orb.process import CommandResult, OnLine

Handler = Callable[[str], "str | Sequence[str] | CommandResult"]
Shell = Callable[[str], str]


class FakeCopilotCli:
    """Stands in for the `copilot` binary behind a CommandExecutor; records argv and any shell commands.

    The handler's return value becomes the streamed JSON event lines: a plain string is wrapped into
    one synthetic `assistant.message_delta` event (so simple tests stay simple); a sequence of strings
    is streamed as-is (each already a JSON event line), letting a test script multiple events and a
    completion signal; a CommandResult is streamed and returned unchanged, including its exit code.
    """

    def __init__(self, handler: Handler | None = None, shell: Shell | None = None) -> None:
        self._handler = handler or (lambda prompt: "")
        self._shell = shell or (lambda command: "")
        self.calls: list[tuple[str, ...]] = []
        self.shell_commands: list[str] = []
        self.terminated = False

    def __call__(
        self,
        command: Sequence[str] | str,
        *,
        timeout_s: float | None = None,
        on_line: OnLine | None = None,
    ) -> CommandResult:
        if isinstance(command, str):
            self.shell_commands.append(command)
            return CommandResult(0, self._shell(command), "")
        argv = tuple(command)
        self.calls.append(argv)
        outcome = self._handler(argv[argv.index("-p") + 1])
        stdout, stderr, exit_code = _as_result(outcome)
        if on_line is not None:
            for line in stdout.splitlines():
                if on_line(line):
                    self.terminated = True
                    break
        return CommandResult(exit_code, stdout, stderr)


def _as_result(outcome: str | Sequence[str] | CommandResult) -> tuple[str, str, int]:
    if isinstance(outcome, CommandResult):
        return outcome.stdout, outcome.stderr, outcome.returncode
    lines = [outcome] if isinstance(outcome, str) else list(outcome)
    return "\n".join(_as_event(line) for line in lines), "", 0


def _as_event(line: str) -> str:
    """A plain string becomes one assistant.message_delta event; a line already JSON passes through."""
    if line.strip().startswith("{"):
        return line
    return json.dumps({"type": "assistant.message_delta", "data": {"deltaContent": line}})


from __future__ import annotations

import shutil
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

from ..clis import AgentProfile
from ..git import GitOptions
from ..observe import LogReading, ToolCall, Usage
from ..run import AgentRequest, RunContext
from ..sessions import CliOutcome, NativeHandle, Resume, Turn


@dataclass(frozen=True)
class FakeToolUse:
    """One tool call the fake agent pretends to make."""

    name: str
    arguments: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    result: str = ""
    ok: bool = True
    seconds: float = 0.5


@dataclass(frozen=True)
class FakeReply:
    """What the fake agent answers: its output and the usage and tools its session log would show."""

    output: str
    usage: Usage = field(default_factory=Usage)
    tools: tuple[FakeToolUse, ...] = ()
    model: str | None = None


Respond = Callable[[AgentRequest, RunContext], FakeReply]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FakeCliRunner:
    """Stands in for an agent CLI: a `CliRunner` that answers from `respond`, and the `SessionLogReader` that reports its usage and tools.

    Pass the same instance as `readers=[...]` to `with_observer` so an observer sees the fake's usage like a real CLI's.
    """

    def __init__(
        self,
        respond: Respond,
        *,
        cli: str = "copilot",
        speed: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.cli = cli
        self._respond = respond
        self._speed = speed
        self._sleep = sleep
        self._clock = clock
        self._readings: dict[str, LogReading] = {}

    def start_args(self, turn: Turn) -> tuple[str, ...]:
        """The fake needs no extra CLI arguments."""
        return ()

    def read(self, turn: Turn) -> LogReading | None:
        """The reading of the turn just run, once; None for an unknown turn."""
        return self._readings.pop(turn.name.value, None)

    def run(self, profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext) -> CliOutcome:
        """Runs one fake turn; whatever `respond` raises propagates as a failed run."""
        reply = self._respond(request, context)
        handle = turn.handle if isinstance(turn, Resume) else NativeHandle(self.cli, f"fake-{uuid.uuid4().hex}")
        started = self._clock()
        tools = self._tool_calls(reply.tools, started)
        self._sleep(sum(use.seconds for use in reply.tools) * self._speed)
        self._readings[turn.name.value] = LogReading(
            native_id=handle.value,
            model=reply.model or profile.model,
            reasoning_effort=profile.reasoning_effort,
            usage=reply.usage,
            tools=tools,
        )
        return CliOutcome(reply.output, handle, 0)

    def _tool_calls(self, uses: tuple[FakeToolUse, ...], started: datetime) -> tuple[ToolCall, ...]:
        calls: list[ToolCall] = []
        cursor = started
        for index, use in enumerate(uses, start=1):
            ended = cursor + timedelta(seconds=use.seconds * self._speed)
            calls.append(ToolCall(f"fake-call-{index}", use.name, use.arguments, use.result, use.ok, cursor, ended))
            cursor = ended
        return tuple(calls)


class DirectoryGit:
    """A `GitService` whose worktree is a fresh temporary directory: no repository, no git, no hooks."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    @contextmanager
    def open(self, cwd: Path, options: GitOptions) -> Iterator[Path]:
        """Yields an empty directory and removes it on exit."""
        path = Path(tempfile.mkdtemp(prefix="loop-fake-", dir=self._root))
        try:
            yield path
        finally:
            shutil.rmtree(path, ignore_errors=True)

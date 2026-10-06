from __future__ import annotations

import sys
import time

import pytest

from orb.errors import CommandError
from orb.process import execute


def _script(*statements: str) -> list[str]:
    return [sys.executable, "-u", "-c", "\n".join(statements)]


def test_on_line_receives_each_line_while_the_process_is_still_running() -> None:
    command = _script(
        "import time",
        "print('a', flush=True)",
        "time.sleep(0.3)",
        "print('b', flush=True)",
    )
    seen: list[tuple[float, str]] = []

    def on_line(line: str) -> bool:
        seen.append((time.monotonic(), line))
        return False

    result = execute(command, on_line=on_line)

    assert [line for _, line in seen] == ["a", "b"]
    assert seen[1][0] - seen[0][0] >= 0.15
    assert result.returncode == 0


def test_the_completion_signal_stops_the_process_before_it_exits_on_its_own() -> None:
    command = _script(
        "import time",
        "print('signal', flush=True)",
        "time.sleep(5)",
        "print('never', flush=True)",
    )
    started = time.monotonic()

    result = execute(command, on_line=lambda line: line == "signal")

    assert time.monotonic() - started < 2
    assert "never" not in result.stdout
    assert result.returncode != 0


def test_the_process_exits_on_its_own_without_the_signal() -> None:
    command = _script("print('done', flush=True)", "raise SystemExit(7)")

    result = execute(command, on_line=lambda line: False)

    assert result.returncode == 7
    assert "done" in result.stdout


def test_a_run_that_exceeds_the_timeout_without_the_signal_raises_a_distinct_error() -> None:
    command = _script("import time", "time.sleep(3)")

    with pytest.raises(CommandError, match="timed out"):
        execute(command, on_line=lambda line: False, timeout_s=0.2)

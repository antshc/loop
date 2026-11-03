"""Adapt repository check commands to workflow verification."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from loop import Fail, Verify
from loop.software_factory.infrastructure.checks import CheckCommand, CheckRunner


def as_verify(
    command: Sequence[str],
    *,
    timeout_s: float | None = None,
    run: CheckRunner | None = None,
) -> Verify:
    """The check `command` as a Verify callable: it runs in the given worktree, and a failure carries its output."""
    check = CheckCommand(command, run=run, timeout_s=timeout_s)

    def verify(path: Path) -> Fail[Any] | None:
        failure = check(path)
        return None if failure is None else Fail(failure)

    return verify

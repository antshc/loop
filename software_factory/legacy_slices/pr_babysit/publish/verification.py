"""The build and test gates every agent edit passes before it is committed."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from software_factory.shared.process import Runner

from ..settings import Repository


class Verifier(Protocol):
    """Raises when the worktree at `path` fails the repository's gates."""

    def __call__(self, path: Path, repository: Repository, timeout: float) -> None: ...


class CommandVerifier:
    """Runs the repository's configured build and test commands without shell interpolation."""

    def __init__(self, run: Runner) -> None:
        self._run = run

    def __call__(self, path: Path, repository: Repository, timeout: float) -> None:
        for command in (repository.build, repository.test):
            self._run(tuple(command), cwd=path, timeout_s=timeout)

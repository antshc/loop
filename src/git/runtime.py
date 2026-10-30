from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Iterator, Protocol

from ..hooks import LoopHookPoint
from .git_cli import GitCli
from .options import GitOptions


class GitService(Protocol):
    def open(self, cwd: Path, options: GitOptions) -> AbstractContextManager[Path]: ...


class GitRuntime:
    """Delegates to the options' strategy and yields the directory the agent works in."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    @contextmanager
    def open(self, cwd: Path, options: GitOptions) -> Iterator[Path]:
        repository = (cwd / options.repository_path) if options.repository_path else cwd
        with options.strategy.open(self._git, cwd, repository, options) as target:
            yield target
        # Reached only when the strategy exited successfully.
        for hook in options.hooks_at(LoopHookPoint.RUN_FINISHED):
            self._git.run_hook(hook, repository, repository, target)

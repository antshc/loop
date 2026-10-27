"""Process-wide (APP scope) registrations of the shared infrastructure."""

from __future__ import annotations

from dishka import Provider, Scope, provide

from .git import GitClient
from .github.account import GitHub
from .github.repo import GhCli, GhRunner
from .process import CommandRunner, Runner


class SharedProvider(Provider):
    """Binds the process runner, Git, and `gh`; a slice overrides any of them with `override=True` providers."""

    scope = Scope.APP

    def __init__(self, *, timeout_s: float | None = None) -> None:
        super().__init__()
        self._timeout_s = timeout_s

    @provide
    def runner(self) -> Runner:
        return CommandRunner(timeout_s=self._timeout_s)

    @provide
    def git(self, run: Runner) -> GitClient:
        return GitClient(run)

    @provide
    def gh(self, run: Runner) -> GhRunner:
        return GhCli(run)

    github = provide(GitHub)

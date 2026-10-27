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


def bind(*bindings: tuple[object, object]) -> Provider:
    """An APP-scope override provider binding each key to a fixed instance; for test fakes and dry runs."""
    provider = Provider(scope=Scope.APP)
    for key, value in bindings:
        provider.provide(_constant(value), provides=key, override=True)
    return provider


def _constant(value: object):
    def factory() -> object:
        return value
    return factory

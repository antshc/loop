"""Dishka registrations: APP scope spans the process, REQUEST scope spans one dev Workflow run."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dishka import Provider, Scope, from_context, provide

from loop import Agent, Phases, SessionLogReader

from workflows.shared.agents.launcher import AgentFactory, AgentLauncher
from workflows.shared.git import GitClient
from workflows.shared.github.issues.client import Issues
from workflows.shared.github.issues.ticketing import SpecTicketingService
from workflows.shared.github.repo import GhCli, GitHubRepo
from workflows.shared.process import Runner
from workflows.shared.providers import bind
from workflows.shared.telemetry import ObserverFactory, OtlpObserverFactory, Telemetry

from ..backlog.router import PullRequestPublisher, SpecRouter
from ..chore.cleanup import ChoreRefiner
from ..deliver.journal import FileExecutionStore
from ..deliver.tickets import TicketDeliverer
from ..model.records import Repository
from ..model.repositories import HARNESS, GithubFactory, Harness, RepositoryWorkflow, Targets, Tracker
from ..model.settings import DevSettings
from ..plan.planner import Planner
from ..review.reviewer import Finalizer, Reviewer
from ..verify.verifier import AppVerifier
from .repositories import configured_repositories


@dataclass(frozen=True)
class SessionReaders:
    """The session-log readers agents report through; None lets each agent read its own."""

    readers: Sequence[SessionLogReader] | None = None


def use_repositories(*workflows: RepositoryWorkflow) -> Provider:
    """Routes Specs to exactly these repository workflows; pass it to `main(providers=...)`."""
    return bind((Targets, Targets(workflows)))


class DevProvider(Provider):
    """Binds every dev collaborator; tests and `--fake` replace any of them with `override=True` providers."""

    settings = from_context(provides=DevSettings, scope=Scope.APP)
    phases = from_context(provides=Phases, scope=Scope.REQUEST)

    @provide(scope=Scope.APP)
    def harness(self) -> Harness:
        return Harness(HARNESS)

    @provide(scope=Scope.APP)
    def targets(self, harness: Harness) -> Targets:
        return configured_repositories(harness)

    @provide(scope=Scope.APP)
    def github_for(self, run: Runner) -> GithubFactory:
        def github(repository: Repository) -> GitHubRepo:
            return GitHubRepo(repository.owner_name.owner, repository.owner_name.repo, gh=GhCli(run, cwd=repository.checkout))
        return github

    @provide(scope=Scope.APP)
    def tracker(self, harness: Harness, github_for: GithubFactory) -> Tracker:
        return Issues(github_for(harness))

    @provide(scope=Scope.APP)
    def ticketing(self, tracker: Tracker) -> SpecTicketingService:
        return SpecTicketingService(tracker)

    @provide(scope=Scope.APP)
    def store(self, settings: DevSettings) -> FileExecutionStore:
        return FileExecutionStore(settings.log_dir)

    @provide(scope=Scope.APP)
    def new_agent(self) -> AgentFactory:
        return Agent

    @provide(scope=Scope.APP)
    def readers(self) -> SessionReaders:
        return SessionReaders()

    @provide(scope=Scope.APP)
    def observers(self) -> ObserverFactory:
        return OtlpObserverFactory("dev", resource_per_agent=True)

    @provide(scope=Scope.APP)
    def telemetry(self, settings: DevSettings, observers: ObserverFactory) -> Telemetry:
        return Telemetry(observers(settings.otlp_endpoint) if settings.otlp_endpoint else None)

    @provide(scope=Scope.APP)
    def launcher(self, new_agent: AgentFactory, telemetry: Telemetry, readers: SessionReaders) -> AgentLauncher:
        return AgentLauncher(new_agent, telemetry.observer, readers.readers)

    planner = provide(Planner, scope=Scope.REQUEST)
    reviewer = provide(Reviewer, scope=Scope.REQUEST)
    finalizer = provide(Finalizer, scope=Scope.REQUEST)
    chore = provide(ChoreRefiner, scope=Scope.REQUEST)
    publisher = provide(PullRequestPublisher, scope=Scope.REQUEST)
    router = provide(SpecRouter, scope=Scope.REQUEST)

    @provide(scope=Scope.REQUEST)
    def deliverer(
        self, git: GitClient, tracker: Tracker, store: FileExecutionStore, phases: Phases,
        settings: DevSettings, harness: Harness,
    ) -> TicketDeliverer:
        return TicketDeliverer(
            git, tracker, store, phases,
            ticket_repository=harness.owner_name, max_failures=settings.max_ticket_failures,
        )

    @provide(scope=Scope.REQUEST)
    def verifier(self, git: GitClient, tracker: Tracker, phases: Phases, settings: DevSettings) -> AppVerifier:
        return AppVerifier(git, tracker, phases, evidence_root=settings.evidence_root)

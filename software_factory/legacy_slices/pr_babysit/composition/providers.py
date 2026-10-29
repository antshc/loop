"""Dishka registrations: APP scope spans the process, REQUEST scope spans one maintained PR."""

from __future__ import annotations

from dishka import Provider, Scope, from_context, provide

from loop import Agent

from software_factory.shared.agents.launcher import AgentFactory, AgentLauncher
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.pull_requests import PullRequests
from software_factory.shared.process import Runner
from software_factory.shared.telemetry import ObserverFactory, OtlpObserverFactory, Telemetry

from ..address.reviews import ReviewAddresser
from ..agents.workspace import PrWorkspace
from ..discover.children import ChildLauncher, ProcessLauncher
from ..discover.scheduler import Scheduler
from ..maintain_ci.failures import CiMaintainer
from ..model import DiscoveryRequest, Pacer, PrState, PrTarget, SleepPacer, StopSignal
from ..monitor.pr import CheckoutGuard, PrMonitor
from ..publish.changes import ChangePublisher
from ..publish.verification import CommandVerifier, Verifier
from ..settings import Repository, Settings


class BabysitProvider(Provider):
    """Binds every babysit collaborator; tests replace any of them with `override=True` providers."""

    settings = from_context(provides=Settings, scope=Scope.APP)
    discovery = from_context(provides=DiscoveryRequest, scope=Scope.APP)
    target = from_context(provides=PrTarget, scope=Scope.REQUEST)

    @provide(scope=Scope.APP)
    def new_agent(self) -> AgentFactory:
        return Agent

    @provide(scope=Scope.APP)
    def observers(self) -> ObserverFactory:
        return OtlpObserverFactory("loop-babysit")

    @provide(scope=Scope.APP)
    def telemetry(self, settings: Settings, observers: ObserverFactory) -> Telemetry:
        # Resolved only by a PR monitor, so the discovery scheduler never initializes telemetry.
        return Telemetry(observers(settings.otel_endpoint) if settings.otel_endpoint else None)

    pacer = provide(SleepPacer, provides=Pacer, scope=Scope.APP)
    stop = provide(StopSignal, scope=Scope.APP)
    children = provide(ProcessLauncher, provides=ChildLauncher, scope=Scope.APP)
    scheduler = provide(Scheduler, scope=Scope.APP)
    guard = provide(CheckoutGuard, scope=Scope.APP)

    @provide(scope=Scope.APP)
    def verifier(self, run: Runner) -> Verifier:
        return CommandVerifier(run)

    @provide(scope=Scope.REQUEST)
    def repository(self, settings: Settings, target: PrTarget) -> Repository:
        return settings.repository(target.repo)

    @provide(scope=Scope.REQUEST)
    def state(self, settings: Settings, target: PrTarget) -> PrState:
        return PrState.of(settings.state_dir, target)

    @provide(scope=Scope.REQUEST)
    def pulls(self, github: GitHub, target: PrTarget) -> PullRequests:
        return github.pull_requests(target.repo)

    @provide(scope=Scope.REQUEST)
    def launcher(self, new_agent: AgentFactory, telemetry: Telemetry) -> AgentLauncher:
        return AgentLauncher(new_agent, telemetry.observer)

    workspace = provide(PrWorkspace, scope=Scope.REQUEST)
    changes = provide(ChangePublisher, scope=Scope.REQUEST)
    addresser = provide(ReviewAddresser, scope=Scope.REQUEST)
    maintainer = provide(CiMaintainer, scope=Scope.REQUEST)
    monitor = provide(PrMonitor, scope=Scope.REQUEST)

"""The composition root: registers services and middlewares, then orders the chain."""

from __future__ import annotations

from collections.abc import Callable

from dishka import Provider, Scope, make_async_container, provide

from .host import AgentHost
from .integrations import (
    CheckRunner,
    NoChecks,
    PlanStore,
    RetryPolicy,
    ReviewChecklist,
    TicketCache,
)
from .middlewares import (
    GitHubTicketMiddleware,
    JsonReplyMiddleware,
    LoggingMiddleware,
    PlanMiddleware,
    RememberPlanMiddleware,
    ReplyRetryMiddleware,
    ResponseFormatMiddleware,
    ResultCheckMiddleware,
    ReviewChecklistMiddleware,
    VerifyChecksMiddleware,
)
from .pipeline import PipelineBuilder
from .terminal import AgentClientTerminal, UnknownRoleTerminal


class MiddlewareProvider(Provider):
    """Middlewares and the terminal are built per call, like scoped services."""

    scope = Scope.REQUEST

    logging = provide(LoggingMiddleware)
    ticket = provide(GitHubTicketMiddleware)
    response_format = provide(ResponseFormatMiddleware)
    retry = provide(ReplyRetryMiddleware)
    json_reply = provide(JsonReplyMiddleware)
    result_check = provide(ResultCheckMiddleware)
    checklist = provide(ReviewChecklistMiddleware)
    remember_plan = provide(RememberPlanMiddleware)
    plan = provide(PlanMiddleware)
    verify = provide(VerifyChecksMiddleware)
    terminal = provide(AgentClientTerminal)
    unknown_role = provide(UnknownRoleTerminal)


class RunProvider(Provider):
    """Services shared by every call of one `AgentHost.run_scope()`."""

    scope = Scope.SESSION

    tickets = provide(TicketCache)
    plans = provide(PlanStore)


class SettingsProvider(Provider):
    """Defaults a caller's provider may override."""

    scope = Scope.APP

    @provide
    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy()

    @provide
    def review_checklist(self) -> ReviewChecklist:
        return ReviewChecklist(("Tests cover the change", "No secrets committed"))

    @provide
    def checks(self) -> CheckRunner:
        return NoChecks()


def configure_pipeline(app: PipelineBuilder) -> None:
    """The middleware order, the `Program.cs` of agent calls; outermost first."""
    app.use(LoggingMiddleware)
    # Shared pre-agent: every role gets the ticket and its reply format.
    app.use(GitHubTicketMiddleware)
    app.use(ResponseFormatMiddleware)
    # Per-role, outside the retry: runs once per call.
    app.for_role("planner", lambda branch: branch.use(RememberPlanMiddleware))
    app.for_role("implementer", lambda branch: branch.use(PlanMiddleware))
    app.for_role("reviewer", lambda branch: branch.use(ReviewChecklistMiddleware))
    app.use(ReplyRetryMiddleware)
    # Per-role, inside the retry: failing checks re-ask the implementer.
    app.for_role("implementer", lambda branch: branch.use(VerifyChecksMiddleware))
    # Shared post-agent.
    app.use(JsonReplyMiddleware)
    app.use(ResultCheckMiddleware)
    app.run(AgentClientTerminal)


def planner_pipeline(chain: PipelineBuilder) -> None:
    """The planner's full chain, outermost first."""
    chain.use(LoggingMiddleware)
    chain.use(GitHubTicketMiddleware)  # pre:  {{TICKET}}
    chain.use(ResponseFormatMiddleware)  # pre:  {{RESPONSE_FORMAT}} from PlanReply
    chain.use(RememberPlanMiddleware)  # post: final PlanReply -> PlanStore
    chain.use(ReplyRetryMiddleware)  # re-ask on ReplyMismatch
    chain.use(JsonReplyMiddleware)  # post: JSON -> PlanReply
    chain.use(ResultCheckMiddleware)  # post: non-zero exit -> AgentRunError
    chain.run(AgentClientTerminal)


def implementer_pipeline(chain: PipelineBuilder) -> None:
    """The implementer's full chain, outermost first."""
    chain.use(LoggingMiddleware)
    chain.use(GitHubTicketMiddleware)  # pre:  {{TICKET}}
    chain.use(ResponseFormatMiddleware)  # pre:  {{RESPONSE_FORMAT}} from ImplementReply
    chain.use(PlanMiddleware)  # pre:  {{PLAN}} from PlanStore
    chain.use(ReplyRetryMiddleware)  # re-ask on ReplyMismatch
    chain.use(VerifyChecksMiddleware)  # post: failing checks -> ReplyMismatch
    chain.use(JsonReplyMiddleware)  # post: JSON -> ImplementReply
    chain.use(ResultCheckMiddleware)  # post: non-zero exit -> AgentRunError
    chain.run(AgentClientTerminal)


def reviewer_pipeline(chain: PipelineBuilder) -> None:
    """The reviewer's full chain, outermost first."""
    chain.use(LoggingMiddleware)
    chain.use(GitHubTicketMiddleware)  # pre:  {{TICKET}}
    chain.use(ResponseFormatMiddleware)  # pre:  {{RESPONSE_FORMAT}} from ReviewReply
    chain.use(ReviewChecklistMiddleware)  # pre:  {{CHECKLIST}}
    chain.use(ReplyRetryMiddleware)  # re-ask on ReplyMismatch
    chain.use(JsonReplyMiddleware)  # post: JSON -> ReviewReply
    chain.use(ResultCheckMiddleware)  # post: non-zero exit -> AgentRunError
    chain.run(AgentClientTerminal)


def configure_role_pipelines(app: PipelineBuilder) -> None:
    """Routes each role to its own chain; a role with no chain fails."""
    app.map_role("planner", planner_pipeline)
    app.map_role("implementer", implementer_pipeline)
    app.map_role("reviewer", reviewer_pipeline)
    app.run(UnknownRoleTerminal)


def bind[T](kind: type[T], value: T) -> Provider:
    """A provider that serves `value` as the APP-scope `kind`."""
    provider = Provider(scope=Scope.APP)
    provider.provide(lambda: value, provides=kind)
    return provider


def create_host(
    *providers: Provider,
    configure: Callable[[PipelineBuilder], None] = configure_pipeline,
) -> AgentHost:
    """The host over the default registrations; later `providers` override earlier ones."""
    container = make_async_container(
        MiddlewareProvider(), RunProvider(), SettingsProvider(), *providers
    )
    app = PipelineBuilder()
    configure(app)
    return AgentHost(container, app.build())

"""implement_spec's composition root: its services and one chain per agent role."""

from __future__ import annotations

from dishka import Provider, Scope, make_async_container, provide

from ..host import AgentHost
from ..integrations import CheckRunner, NoChecks, RetryPolicy
from ..middlewares import (
    AgentCommitsMiddleware,
    JsonReplyMiddleware,
    LoggingMiddleware,
    ReadOnlyMiddleware,
    ReplyRetryMiddleware,
    ResultCheckMiddleware,
    StatusEnvelopeMiddleware,
    StatusFormatMiddleware,
)
from ..pipeline import PipelineBuilder
from ..terminal import AgentClientTerminal, UnknownRoleTerminal
from .middlewares import (
    BranchCommitsMiddleware,
    ChoreCommitMiddleware,
    DiffCaptureMiddleware,
    InitiativeCommitsMiddleware,
    PlanPolicyMiddleware,
    PullRequestMiddleware,
    RouteMiddleware,
    SpecJsonMiddleware,
    TicketJsonMiddleware,
)
from .policy import PlanPolicy


class ImplementSpecMiddlewares(Provider):
    """Every middleware and terminal the role chains use, built per call."""

    scope = Scope.REQUEST

    logging = provide(LoggingMiddleware)
    route = provide(RouteMiddleware)
    spec_json = provide(SpecJsonMiddleware)
    ticket_json = provide(TicketJsonMiddleware)
    initiative_commits = provide(InitiativeCommitsMiddleware)
    branch_commits = provide(BranchCommitsMiddleware)
    pull_request = provide(PullRequestMiddleware)
    diff_capture = provide(DiffCaptureMiddleware)
    status_format = provide(StatusFormatMiddleware)
    plan_policy = provide(PlanPolicyMiddleware)
    retry = provide(ReplyRetryMiddleware)
    read_only = provide(ReadOnlyMiddleware)
    agent_commits = provide(AgentCommitsMiddleware)
    chore_commit = provide(ChoreCommitMiddleware)
    json_reply = provide(JsonReplyMiddleware)
    status_envelope = provide(StatusEnvelopeMiddleware)
    result_check = provide(ResultCheckMiddleware)
    terminal = provide(AgentClientTerminal)
    unknown_role = provide(UnknownRoleTerminal)


class ImplementSpecSettings(Provider):
    """Defaults a caller's provider may override."""

    scope = Scope.APP

    @provide
    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy()

    @provide
    def plan_policy(self) -> PlanPolicy:
        return PlanPolicy()

    @provide
    def checks(self) -> CheckRunner:
        return NoChecks()


def planner_pipeline(chain: PipelineBuilder) -> None:
    """plan-spec: read-only on the checkout; a decline holds, an invalid plan fails."""
    chain.use(LoggingMiddleware)
    chain.use(RouteMiddleware)  # pre:  {{WORKTREE_PATH}}, branches
    chain.use(SpecJsonMiddleware)  # pre:  {{SPEC_JSON}} from the tracker
    chain.use(StatusFormatMiddleware)  # pre:  {{RESPONSE_FORMAT}} envelope of Proposed
    chain.use(PlanPolicyMiddleware)  # post: plan_violation -> ReplyRejected
    chain.use(ReplyRetryMiddleware)  # re-ask on ReplyMismatch
    chain.use(ReadOnlyMiddleware)  # post: moved HEAD or tree -> restore, ReplyRejected
    chain.use(JsonReplyMiddleware)  # post: payload -> Proposed
    chain.use(StatusEnvelopeMiddleware)  # post: failed -> AgentDeclined
    chain.use(ResultCheckMiddleware)  # post: non-zero exit -> AgentRunError
    chain.run(AgentClientTerminal)


def developer_pipeline(chain: PipelineBuilder) -> None:
    """implement-ticket: exactly one agent commit per attempt, echoing the identifier."""
    chain.use(LoggingMiddleware)
    chain.use(RouteMiddleware)  # pre:  {{WORKTREE_PATH}}, branches
    chain.use(TicketJsonMiddleware)  # pre:  {{TICKET_JSON}} from the tracker
    chain.use(InitiativeCommitsMiddleware)  # pre:  {{INITIATIVE_COMMITS}} from git
    chain.use(StatusFormatMiddleware)  # pre:  {{RESPONSE_FORMAT}} with identifier
    chain.use(ReplyRetryMiddleware)  # re-ask on ReplyMismatch
    chain.use(AgentCommitsMiddleware)  # pre: {{COMMIT_SUBJECT_PREFIX}}; post: contract
    chain.use(JsonReplyMiddleware)  # post: payload -> Delivered
    chain.use(StatusEnvelopeMiddleware)  # post: wrong identifier -> ReplyMismatch
    chain.use(ResultCheckMiddleware)  # post: non-zero exit -> AgentRunError
    chain.run(AgentClientTerminal)


def chore_pipeline(chain: PipelineBuilder) -> None:
    """refine-chore: edits stay uncommitted; Loop verifies and commits them."""
    chain.use(LoggingMiddleware)
    chain.use(RouteMiddleware)  # pre:  {{WORKTREE_PATH}}, branches
    chain.use(PullRequestMiddleware)  # pre:  {{PULL_REQUEST_URL}}
    chain.use(BranchCommitsMiddleware)  # pre:  {{BRANCH_COMMITS}} from git
    chain.use(DiffCaptureMiddleware)  # pre:  {{DIFF_DIR}}; removed after the call
    chain.use(StatusFormatMiddleware)  # pre:  {{RESPONSE_FORMAT}} envelope of Refined
    chain.use(ReplyRetryMiddleware)  # re-ask on ReplyMismatch
    chain.use(ChoreCommitMiddleware)  # post: verify, commit chore(...); else restore
    chain.use(JsonReplyMiddleware)  # post: payload -> Refined
    chain.use(StatusEnvelopeMiddleware)  # post: failed -> AgentDeclined
    chain.use(ResultCheckMiddleware)  # post: non-zero exit -> AgentRunError
    chain.run(AgentClientTerminal)


def reviewer_pipeline(chain: PipelineBuilder) -> None:
    """review-pull-request: read-only on the checkout; finalize judges the reply."""
    chain.use(LoggingMiddleware)
    chain.use(RouteMiddleware)  # pre:  {{WORKTREE_PATH}}, {{INITIATIVE}}
    chain.use(PullRequestMiddleware)  # pre:  {{PULL_REQUEST_URL}}
    chain.use(StatusFormatMiddleware)  # pre:  {{RESPONSE_FORMAT}} envelope of Reviewed
    chain.use(ReplyRetryMiddleware)  # re-ask on ReplyMismatch
    chain.use(ReadOnlyMiddleware)  # post: moved HEAD or tree -> restore, ReplyRejected
    chain.use(JsonReplyMiddleware)  # post: payload -> Reviewed
    chain.use(StatusEnvelopeMiddleware)  # post: failed -> AgentDeclined
    chain.use(ResultCheckMiddleware)  # post: non-zero exit -> AgentRunError
    chain.run(AgentClientTerminal)


def configure_implement_spec(app: PipelineBuilder) -> None:
    """Routes each software-factory role to its own chain; any other role fails."""
    app.map_role("planner", planner_pipeline)
    app.map_role("developer", developer_pipeline)
    app.map_role("chore", chore_pipeline)
    app.map_role("reviewer", reviewer_pipeline)
    app.run(UnknownRoleTerminal)


def create_implement_spec_host(*providers: Provider) -> AgentHost:
    """The host of the four roles; `providers` bind `Tracker`, `WorktreeGit`, `AgentClient` and overrides."""
    container = make_async_container(
        ImplementSpecMiddlewares(), ImplementSpecSettings(), *providers
    )
    app = PipelineBuilder()
    configure_implement_spec(app)
    return AgentHost(container, app.build())

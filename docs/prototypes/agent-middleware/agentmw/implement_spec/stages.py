"""implement_spec's Stages and hand-offs: each asks one role or one tracker question and returns a verdict."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager

from ..context import AgentRequest
from ..errors import AgentDeclined, AgentError
from ..flow import Fail, Hold, Item, Pass, Selector, Stage, Verdict, Workspace
from . import templates
from .messages import Delivered, Proposed, Refined, Reviewed
from .policy import HITL, SOURCE, review_blockers, ticket_subject_prefix
from .state import Delivery, Outcome, SpecRoute, SpecRun
from .tracker import PullRequest

type Step = Stage[SpecRun]


# Workspaces: where the agent calls of a flow run.


def checkout() -> Workspace[SpecRun]:
    """The read-only checkout."""
    return _at(lambda route: route.checkout)


def feature_worktree() -> Workspace[SpecRun]:
    """The worktree the agents commit in."""
    return _at(lambda route: route.worktree)


# Plan.


def plan_spec() -> Step:
    """Read-only planner, skipped when the Spec has Tickets; a decline holds, an invalid plan fails."""

    async def action(ctx: SpecRun) -> Verdict:
        request = AgentRequest(
            "planner", templates.PLANNER, Proposed, metadata=ctx.metadata()
        )
        return await _answer(ctx, request, declined=Hold)

    return Stage("plan-spec", action).when(_needs_plan)


def create_tickets() -> Step:
    """Creates the Tickets of the plan on the tracker, with their blocked-by edges."""

    async def action(ctx: SpecRun) -> Verdict:
        proposed: Proposed = ctx.results["plan-spec"]
        numbers = await ctx.tracker.create_tickets(ctx.route.spec, proposed.tickets)
        await ctx.comment(f"plan: {_numbers(numbers)}")
        return Pass(numbers)

    return Stage("create-tickets", action).when(_planned)


# Deliver.


def publish_draft_pr() -> Step:
    """Opens the draft pull request once the feature branch has commits; passes with None before."""

    async def action(ctx: SpecRun) -> Verdict:
        if not await ctx.git.branch_commits(ctx.route.worktree):
            return Pass(None)
        spec = await ctx.tracker.spec(ctx.route.spec)
        return Pass(
            await ctx.tracker.publish_draft(ctx.route.feature_branch, spec.title)
        )

    return Stage("publish-draft-pr", action)


def require_work() -> Step:
    """Passes when a Ticket is ready; holds when none is."""

    async def action(ctx: SpecRun) -> Verdict:
        ready = await _ready(ctx)
        if not ready:
            return Hold("the Spec has no actionable Ticket")
        return Pass(tuple(item.value.number for item in ready))

    return Stage("require-work", action)


def next_ready_ticket() -> Selector[SpecRun]:
    """The READY Tickets, re-read from the tracker, which owns Ticket state."""
    return Selector("next-ready-ticket", _ready)


def implement_ticket() -> Step:
    """Fresh developer call per attempt; a decline or a broken commit contract fails."""

    async def action(ctx: SpecRun) -> Verdict:
        initiative = ctx.route.initiative
        number = ctx.item.number
        metadata = ctx.metadata(
            ticket=str(number),
            identifier=f"{initiative}|{number}",
            commit_prefix=ticket_subject_prefix(initiative, number),
        )
        request = AgentRequest(
            "developer", templates.DEVELOPER, Delivered, metadata=metadata
        )
        return await _answer(ctx, request)

    return Stage("implement-ticket", action)


def close_ticket() -> Step:
    """Closes the Ticket with the delivery comment."""

    async def action(ctx: SpecRun) -> Verdict:
        delivered: Delivered = ctx.results["implement-ticket"]
        comment = f"Delivered in {delivered.commit}.\n\n{delivered.summary}\n\n{delivered.verification}"
        await ctx.tracker.close(ctx.item.number, comment)
        return Pass(ctx.item.number)

    return Stage("close-ticket", action)


def require_delivered() -> Step:
    """Fails after any escalation, holds while Tickets remain open, otherwise passes with the delivery."""

    async def action(ctx: SpecRun) -> Verdict:
        if ctx.escalated:
            return Fail(
                f"Tickets were escalated to a person in this run: {_numbers(ctx.escalated)}"
            )
        spec = await ctx.tracker.spec(ctx.route.spec)
        if remaining := [ticket.number for ticket in spec.open_tickets()]:
            return Hold(f"Tickets remain open: {_numbers(remaining)}")
        commits = await ctx.git.branch_commits(ctx.route.worktree)
        head = await ctx.git.head(ctx.route.worktree)
        return Pass(Delivery(head, tuple(f"{c.sha[:7]} {c.subject}" for c in commits)))

    return Stage("require-delivered", action)


def announce_delivered() -> Step:
    """Tells the Spec where the draft pull request is; holds when nothing was committed."""

    async def action(ctx: SpecRun) -> Verdict:
        pull_request: PullRequest | None = ctx.results["publish-delivery"]
        if pull_request is None:
            return Hold("nothing was committed")
        await ctx.comment(f"Tickets delivered in {pull_request.url}")
        return Pass(pull_request.url)

    return Stage("announce-delivered", action)


def refine_chore() -> Step:
    """Best-effort chore on the feature worktree, skipped without a pull request; Loop commits what passes."""

    async def action(ctx: SpecRun) -> Verdict:
        request = AgentRequest(
            "chore", templates.CHORE, Refined, metadata=ctx.metadata()
        )
        return await _answer(ctx, request)

    return Stage("refine-chore", action).when(_has_pull_request)


# Review and finalize.


def review_pull_request() -> Step:
    """Read-only reviewer on the checkout, skipped without a pull request; finalize judges what it found."""

    async def action(ctx: SpecRun) -> Verdict:
        delivery: Delivery = ctx.results["require-refined"]
        request = AgentRequest(
            "reviewer",
            templates.REVIEWER,
            Reviewed,
            args={"HEAD_SHA": delivery.head},
            metadata=ctx.metadata(),
        )
        return await _answer(ctx, request)

    return Stage("review-pull-request", action).when(_has_pull_request)


def finalize() -> Step:
    """Marks the pull request ready only when nothing blocks it; otherwise keeps it a draft and labels the Spec. Never merges."""

    async def action(ctx: SpecRun) -> Verdict:
        pull_request = await ctx.tracker.open_pull_request(ctx.route.feature_branch)
        blockers = await _blockers(ctx, pull_request)
        url = None if pull_request is None else pull_request.url
        if blockers or pull_request is None:
            return Pass(await _stay_draft(ctx, "; ".join(blockers), url))
        await ctx.tracker.mark_ready(pull_request)
        await ctx.comment(f"{url} is ready for review")
        return Pass(Outcome("ready", pull_request=url))

    return Stage("finalize", action)


# Hand-offs: what a hold, a failure or an escalation does to the tracker.


async def hand_to_human(ctx: SpecRun, reason: str) -> None:
    """Comments why a person is needed, then labels the Spec."""
    await ctx.comment(reason)
    await ctx.tracker.add_label(ctx.route.spec, HITL)


async def escalate_ticket(ctx: SpecRun, reason: str) -> None:
    """Comments on the Ticket and the Spec, then labels both; the labels come last, so both mean it finished."""
    ticket = ctx.item.number
    message = f"{SOURCE}: {reason}"
    await ctx.tracker.comment(ticket, message)
    await ctx.tracker.comment(ctx.route.spec, message)
    await ctx.tracker.add_label(ticket, HITL)
    await ctx.tracker.add_label(ctx.route.spec, HITL)
    ctx.escalated.append(ticket)


async def comment_failure(ctx: SpecRun, reason: str) -> None:
    """Comments on the Spec that its run failed."""
    await ctx.comment(f"the run failed: {reason}")


# Helpers.


async def _answer[R](
    ctx: SpecRun,
    request: AgentRequest[R],
    declined: Callable[[str], Verdict] = Fail,
) -> Verdict:
    try:
        return Pass(await ctx.agents.call(request))
    except AgentDeclined as exception:
        return declined(exception.reason)
    except AgentError as exception:
        return Fail(str(exception))


async def _ready(ctx: SpecRun) -> list[Item]:
    spec = await ctx.tracker.spec(ctx.route.spec)
    return [Item(t, f"#{t.number}") for t in spec.ready_tickets(HITL)]


async def _needs_plan(ctx: SpecRun) -> bool:
    return not (await ctx.tracker.spec(ctx.route.spec)).tickets


async def _planned(ctx: SpecRun) -> bool:
    return "plan-spec" in ctx.results


async def _has_pull_request(ctx: SpecRun) -> bool:
    return await ctx.tracker.open_pull_request(ctx.route.feature_branch) is not None


async def _blockers(ctx: SpecRun, pull_request: PullRequest | None) -> list[str]:
    delivery: Delivery = ctx.results["require-refined"]
    review = ctx.results.get("review-pull-request") or ctx.failures.get(
        "review-pull-request"
    )
    blockers = review_blockers(review, delivery.head)
    if await ctx.git.head(ctx.route.worktree) != delivery.head:
        blockers.append("the branch head moved after the review started")
    if pull_request is None:
        blockers.append("there is no open pull request to mark ready")
    return blockers


async def _stay_draft(ctx: SpecRun, reason: str, url: str | None) -> Outcome:
    await ctx.comment(f"{url or 'the pull request'} stays a draft: {reason}")
    await ctx.tracker.add_label(ctx.route.spec, HITL)
    return Outcome("draft", reason, url)


def _numbers(numbers: tuple[int, ...] | list[int]) -> str:
    return ", ".join(f"#{number}" for number in numbers)


def _at(select: Callable[[SpecRoute], str]) -> Workspace[SpecRun]:
    @asynccontextmanager
    async def workspace(ctx: SpecRun):
        previous, ctx.path = ctx.path, select(ctx.route)
        try:
            yield
        finally:
            ctx.path = previous

    return workspace

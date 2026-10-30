"""The address Stages: group pending feedback into issues, resolve each issue under the runtime's commit contract, and require that nothing was unclear."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from software_factory.agents.roles.address import (
    GROUP_FEEDBACK, RESOLVE_ISSUE, FeedbackIssue, Grouped, GroupRequest, Resolved, ResolveRequest,
)
from software_factory.events.feedback import (
    FeedbackBatch, FeedbackGrouped, GroupedIssue, IssueResolved, IssueUnclear, ReplyDraft,
)
from software_factory.events.pull_requests import PullRequestPolled
from software_factory.runtime import (
    Evidence, Fail, HarnessCommits, History, Hold, Item, Nothing, Pass, ReadOnly, Retain, Selector, Stage, Verdict, Verify,
)

CLARIFICATION_REQUESTED = "Reviewer clarification requested"

_ISSUE_ID = re.compile(r"[a-zA-Z0-9_-]{1,80}")
_CITATION = re.compile(r"\S+:\d+")


@dataclass(frozen=True)
class GroupInput:
    batch: FeedbackBatch
    grouped: History[FeedbackGrouped]


@dataclass(frozen=True)
class IssueWork:
    issue: GroupedIssue
    batch: FeedbackBatch
    polled: PullRequestPolled
    resolved: History[IssueResolved]
    unclear: History[IssueUnclear]


@dataclass(frozen=True)
class UnclearInput:
    batch: FeedbackBatch
    unclear: History[IssueUnclear]


def group_feedback() -> Stage[GroupInput, Grouped, FeedbackGrouped]:
    """Read-only address agent groups the batch's feedback into issues; every item must be covered by exactly one issue. A grouping an earlier run recorded for the same batch is reused."""
    return (
        Stage("group-feedback", GroupInput, produces=FeedbackGrouped)
        .agent(GROUP_FEEDBACK, request=lambda i, _: GroupRequest(list(i.batch.items)))
        .gate(_grouped)
        .contract(ReadOnly())
        .when(lambda i: _grouping(i.batch, i.grouped) is None)
    )


def issues() -> Selector[GroupInput, GroupedIssue]:
    """The batch's grouped issues in order; every issue's revision is the head the batch opened at, so a resumed batch sees the same items."""
    return Selector("issues", GroupInput, GroupedIssue, _grouped_issues)


def resolve_issue(*, message: Callable[[IssueWork], str], verify: Verify) -> Stage[IssueWork, Resolved, IssueResolved]:
    """One issue: unclear holds with the drafted replies; a fix without changes, no change with changes and missing evidence fail; the runtime commits a verified fix with `message(work)` and retains the worktree when anything fails. An issue an earlier run decided is skipped."""
    return (
        Stage("resolve-issue", IssueWork, produces=IssueResolved, may_emit=(IssueUnclear,))
        .agent(RESOLVE_ISSUE, request=_resolve_request)
        .gate(_resolution)
        .contract(HarnessCommits(message=lambda i, _: message(i), verify=verify, on_fail=Retain()))
        .when(lambda i: not _decided(i))
        .attributes(lambda i: {"issue": i.issue.id})
    )


def require_no_unclear() -> Stage[UnclearInput, tuple[IssueUnclear, ...], Nothing]:
    """Holds with `Reviewer clarification requested` when any issue of the batch was unclear."""
    return (
        Stage("require-no-unclear", UnclearInput, produces=Nothing)
        .action(lambda i: tuple(event for event in i.unclear if event.batch == i.batch.token))
        .gate(_none_unclear)
    )


def _grouping(batch: FeedbackBatch, grouped: Sequence[FeedbackGrouped]) -> FeedbackGrouped | None:
    """The latest grouping recorded for `batch`."""
    return next((event for event in reversed(grouped) if event.batch == batch.token), None)


def _grouped_issues(i: GroupInput) -> Sequence[Item[GroupedIssue]]:
    grouping = _grouping(i.batch, i.grouped)
    return [] if grouping is None else [Item(issue, issue.id, grouping.base) for issue in grouping.issues]


def _decided(i: IssueWork) -> bool:
    """An earlier run already resolved the issue or found it unclear."""
    return any(event.batch == i.batch.token and event.issue == i.issue.id for event in (*i.resolved, *i.unclear))


def _resolve_request(i: IssueWork, _: Path) -> ResolveRequest:
    items = {item["key"]: item for item in i.batch.items}
    return ResolveRequest(FeedbackIssue(i.issue.id, i.issue.statement, i.issue.keys), [items[key] for key in i.issue.keys])


def _grouped(i: GroupInput, reply: Grouped, _: Evidence) -> Verdict[FeedbackGrouped]:
    problem = _coverage_problem(i.batch, reply)
    if problem is not None:
        return Fail(problem)
    grouped = tuple(GroupedIssue(issue.id, issue.statement, issue.keys) for issue in reply.issues)
    return Pass(FeedbackGrouped(i.batch.token, i.batch.head, grouped))


def _coverage_problem(batch: FeedbackBatch, reply: Grouped) -> str | None:
    """Why the grouping may not be edited against; None when every item is covered by exactly one issue."""
    if not reply.issues:
        return "Address returned no issues"
    if any(not _ISSUE_ID.fullmatch(issue.id) or not issue.keys for issue in reply.issues):
        return "Address returned an invalid issue"
    ids = [issue.id for issue in reply.issues]
    keys = [key for issue in reply.issues for key in issue.keys]
    if len(ids) != len(set(ids)) or sorted(keys) != sorted(batch.keys):
        return "Address must assign every feedback item exactly once"
    return None


def _resolution(i: IssueWork, reply: Resolved, evidence: Evidence) -> Verdict[IssueResolved]:
    drafts = tuple(ReplyDraft(draft.key, draft.body) for draft in reply.replies)
    problem = _resolution_problem(i, reply, evidence)
    if problem is not None:
        return Fail(problem)
    if reply.verdict == "unclear":
        return Hold(CLARIFICATION_REQUESTED, IssueUnclear(i.batch.token, i.issue.id, reply.reason, drafts))
    return Pass(IssueResolved(i.batch.token, i.issue.id, reply.verdict, drafts))


def _resolution_problem(i: IssueWork, reply: Resolved, evidence: Evidence) -> str | None:
    """Why the resolution is refused; None when the verdict, its evidence and what the worktree shows agree."""
    replies = {draft.key: draft.body for draft in reply.replies}
    if set(replies) != set(i.issue.keys) or any(not body.strip() for body in replies.values()):
        return "Address must draft one reply for every feedback item"
    if reply.verdict != "unclear" and not any(_CITATION.search(citation) for citation in reply.evidence):
        return "Address verdict requires path:line evidence"
    if reply.verdict == "fix-needed" and not evidence.changed:
        return "Agent claimed a fix but produced no changes"
    if reply.verdict != "fix-needed" and evidence.changed:
        what = "no change needed" if reply.verdict == "no-change-needed" else "unclear"
        return f"Agent reported {what} but changed files"
    return None


def _none_unclear(_: UnclearInput, unclear: tuple[IssueUnclear, ...], __: Evidence) -> Verdict[Nothing]:
    return Hold(CLARIFICATION_REQUESTED) if unclear else Pass(Nothing())

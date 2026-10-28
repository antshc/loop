"""In-memory issue models for tests that need no `gh` fake."""

from __future__ import annotations

from workflows.shared.github.identity import IssueIdentity, IssueRef, RepositoryId
from workflows.shared.github.issues.graph import SpecGraphSnapshot
from workflows.shared.github.issues.issue import Issue, IssueState
from workflows.shared.github.issues.spec import Spec, Ticket
from workflows.shared.github.pull_requests.model import (
    CheckKind,
    CheckOutcome,
    CheckResult,
    Mergeability,
    MergeState,
    PullRequest,
    PullRequestReview,
    PullRequestSnapshot,
    PullRequestState,
    ReviewThread,
    ReviewVerdict,
)


REPOSITORY = RepositoryId("owner", "repo")


def build_issue(
    number: int = 1,
    title: str = "Add login page",
    *,
    labels: tuple[str, ...] = (),
    state: IssueState = IssueState.OPEN,
    issue_type: str | None = None,
    body: str = "",
) -> Issue:
    """An issue with stable identity derived from its number."""
    return Issue(
        ref=IssueRef(REPOSITORY, number),
        identity=IssueIdentity(1000 + number, f"I_kwDO{number}"),
        title=title,
        body=body,
        url=f"https://github.com/{REPOSITORY}/issues/{number}",
        state=state,
        labels=labels,
        issue_type=issue_type,
    )


def build_spec(number: int = 1, title: str = "Add login page", labels: tuple[str, ...] = ("spec",)) -> Spec:
    """A Spec carrying `labels`."""
    return Spec.of(build_issue(number, title, labels=labels))


def build_ticket(number: int = 10, title: str = "Add login form", labels: tuple[str, ...] = (), state: IssueState = IssueState.OPEN) -> Ticket:
    """A Ticket in `state` carrying `labels`."""
    return Ticket.of(build_issue(number, title, labels=labels, state=state))


def build_graph(
    number: int = 1,
    title: str = "Add login page",
    labels: tuple[str, ...] = ("spec",),
    tickets: tuple[Ticket, ...] = (),
) -> SpecGraphSnapshot:
    """A complete Spec graph whose `tickets` were all read open or held."""
    return SpecGraphSnapshot(
        spec=build_spec(number, title, labels),
        tickets=tickets,
        sub_issue_total=len(tickets),
        held_tickets=tuple(ticket.number for ticket in tickets if not ticket.actionable),
    )


def build_pull_request_snapshot(
    *,
    draft: bool = False,
    state: PullRequestState = PullRequestState.OPEN,
    mergeability: Mergeability = Mergeability.MERGEABLE,
    merge_state: MergeState = MergeState.CLEAN,
    base_ref: str = "main",
    head_sha: str = "head",
    author: str = "me",
    reviews: tuple[PullRequestReview, ...] = (),
    threads: tuple[ReviewThread, ...] = (),
    checks: tuple[CheckResult, ...] = (),
    review_decision: str = "",
) -> PullRequestSnapshot:
    """An in-memory pull request snapshot that is open, mergeable, and has nothing else unless given."""
    pull = PullRequest(
        ref=IssueRef(REPOSITORY, 7),
        identity=IssueIdentity(9007, "PR_7"),
        title="Add login",
        body="",
        url=f"https://github.com/{REPOSITORY}/pull/7",
        state=state,
        draft=draft,
        merged=False,
        base_ref=base_ref,
        head_ref="feature",
        head_sha=head_sha,
        head_repository=str(REPOSITORY),
        author=author,
        mergeability=mergeability,
        merge_state=merge_state,
    )
    return PullRequestSnapshot(pull, threads, reviews, (), checks, review_decision)


def build_review(id: int, reviewer: str, verdict: ReviewVerdict, *, commit: str = "head") -> PullRequestReview:
    """A review of the pull request."""
    return PullRequestReview(id, reviewer, verdict, commit, "", "2026-03-01T12:00:00Z")


def build_thread(id: str = "t1", *, resolved: bool = False) -> ReviewThread:
    """A review thread with no comments."""
    return ReviewThread(id, resolved, False, "cart.py", 4, 4, ())


def build_check(
    name: str,
    outcome: CheckOutcome = CheckOutcome.PASSED,
    *,
    commit: str = "head",
    kind: CheckKind = CheckKind.CHECK_RUN,
    id: int = 1,
) -> CheckResult:
    """A check result of `kind` that ran against `commit`."""
    return CheckResult(kind, id, name, commit, outcome)

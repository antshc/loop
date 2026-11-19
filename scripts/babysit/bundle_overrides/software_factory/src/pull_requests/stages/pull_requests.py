"""The pull request Stages: validate the checkout, take a snapshot, decide the next action, publish a CI fix and block the pull request."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loop import AgentWorkspace, Evidence, Fail, History, Hold, Pass, Stage, Verdict
from loop.shared.git import GitClient
from loop.software_factory.infrastructure.github.repo import CommandError

from software_factory.src.github import GitHub
from software_factory.src.pull_requests.events.ci import CiFixed
from software_factory.src.pull_requests.events.feedback import (
    DiscussionAddressed,
    DiscussionPending,
    FeedbackHandled,
)
from software_factory.src.pull_requests.events.pull_requests import (
    CiFailing,
    CopilotDue,
    CopilotRequested,
    DraftWaiting,
    MergeConflict,
    NextAction,
    OriginValidated,
    PrBlocked,
    PullRequestClosed,
    PullRequestPolled,
    PullRequestWork,
    ReadinessCheck,
    Unsupported,
)
from software_factory.src.pull_requests.infrastructure.github.pull_requests import (
    PullRequests,
)
from software_factory.src.pull_requests.infrastructure.github.pull_requests.babysit import (
    babysit_snapshot,
    bot_logins,
    ci_failed,
    ci_state,
    head_confirmed,
    ignore_checks,
    is_copilot,
)
from software_factory.src.pull_requests.infrastructure.github.pull_requests.discussion_state import (
    compact,
    discussion_token,
    gate,
)

_UNSUPPORTED = "PR author changed or fork head is not supported by this checkout"
_UNREADY_DECISIONS = {"REVIEW_REQUIRED", "CHANGES_REQUESTED"}


@dataclass(frozen=True)
class OriginInput:
    work: PullRequestWork
    git: GitClient


@dataclass(frozen=True)
class SnapshotInput:
    work: PullRequestWork
    github: GitHub


@dataclass(frozen=True)
class DecideInput:
    polled: PullRequestPolled
    addressed: History[DiscussionAddressed]
    requested: History[CopilotRequested]


@dataclass(frozen=True)
class BlockInput:
    work: PullRequestWork
    polled: History[PullRequestPolled]
    unsupported: History[Unsupported]
    github: GitHub


@dataclass(frozen=True)
class PublishInput:
    worktree: AgentWorkspace
    batch: CiFixed
    polled: PullRequestPolled
    git: GitClient
    github: GitHub


@dataclass(frozen=True)
class _Refused:
    reason: str


def validate_origin() -> Stage[OriginInput, str, OriginValidated]:
    """Holds unless the checkout's origin is the pull request's repository, so nothing is read or edited in the wrong one."""
    return (
        Stage("validate-origin", OriginInput, produces=OriginValidated)
        .action(lambda i: i.git.origin_slug(i.work.target.checkout))
        .gate(_origin_matches)
    )


def read(work: PullRequestWork, github: GitHub) -> PullRequestPolled:
    """One read of the pull request exactly as the snapshot Stage takes it, for an intake that decides before any run."""
    return _poll(SnapshotInput(work, github))


def snapshot() -> Stage[SnapshotInput, PullRequestPolled, PullRequestPolled]:
    """Reads the pull request through the typed model and projects it to the dictionaries babysit's fingerprints and agent envelopes are built on."""
    return (
        Stage("snapshot", SnapshotInput, produces=PullRequestPolled)
        .action(_poll)
        .gate(lambda _, polled, __: Pass(polled))
    )


def decide_next(
    *,
    author: str | None,
    copilot: Callable[[PullRequestPolled], bool],
) -> Stage[DecideInput, NextAction, NextAction]:
    """Pure: closed, unsupported, draft (watch only), unknown mergeability, conflict, review discussion the address gate reports, failed CI, Copilot, then readiness; `author` defaults to the acting account."""
    return (
        Stage("decide-next", DecideInput, produces=NextAction)
        .action(lambda i: _decide(i, author, copilot))
        .gate(lambda _, action, __: Pass(action))
    )


def block_pr(
    *, fingerprint: Callable[[PullRequestPolled], str]
) -> Stage[BlockInput, PrBlocked, PrBlocked]:
    """Hand-off that blocks the pull request: re-reads it so the fingerprint, computed by `fingerprint`, includes our own replies, and records the blocking reason and that fingerprint as run attributes for intake."""
    return (
        Stage("block-pr", BlockInput, produces=PrBlocked)
        .effect(lambda i: _blocked(i, fingerprint), reconcile=lambda _: None)
        .gate(lambda _, blocked, __: Pass(blocked))
        .records_run(_blocked_attributes)
    )


def publish_batch() -> Stage[PublishInput, FeedbackHandled | _Refused, FeedbackHandled]:
    """Pushes the CI fix in the worktree head without force when it is ahead and confirms the remote head equals it; a crash after the push is reconciled from the remote head."""
    return (
        Stage("publish-batch", PublishInput, produces=FeedbackHandled)
        .effect(lambda i: _published(i) or _publish(i), reconcile=_published)
        .gate(_accepted)
    )


def _accepted(
    _: PublishInput, reply: FeedbackHandled | _Refused, __: Evidence
) -> Verdict[FeedbackHandled]:
    return Fail(reply.reason) if isinstance(reply, _Refused) else Pass(reply)


def _published(i: PublishInput) -> FeedbackHandled | None:
    """The publication a crashed attempt completed: the remote head is the worktree head."""
    pull, local = (
        _pulls(i).pull_request(i.polled.number),
        i.git.head(i.worktree.path).sha,
    )
    if not pull.is_open or pull.head_ref != i.polled.branch or pull.head_sha != local:
        return None
    return FeedbackHandled((), local)


def _publish(i: PublishInput) -> FeedbackHandled | _Refused:
    pulls, number, path = _pulls(i), i.polled.number, i.worktree.path
    local, pull = i.git.head(path).sha, pulls.pull_request(number)
    if not pull.is_open:
        return _Refused(
            "PR closed during publication; inspect retained work before retrying"
        )
    if pull.head_ref != i.polled.branch:
        return _Refused("PR branch changed during publication")
    if pull.head_sha not in {i.batch.head, local}:
        return _Refused("PR head moved; publication stopped before pushing")
    if local != i.batch.head and pull.head_sha != local:
        i.git.publish(path, i.polled.branch)
    if not head_confirmed(lambda: pulls.pull_request(number).head_sha, local):
        return _Refused("Published SHA could not be confirmed")
    return FeedbackHandled((), local)


def _pulls(i: PublishInput) -> PullRequests:
    return i.github.pull_requests(i.polled.target.slug)


def _origin_matches(i: OriginInput, slug: str, _: Evidence) -> Verdict[OriginValidated]:
    target = i.work.target
    if slug.lower() == target.slug.lower():
        return Pass(OriginValidated(slug))
    return Hold(f"Origin does not match {target.slug}: {target.checkout}")


def _poll(i: SnapshotInput) -> PullRequestPolled:
    target, number = i.work.target, i.work.number
    read = i.github.pull_requests(target.slug).snapshot(number)
    # Plain JSON, so the Event equals what the ledger replays.
    projected = json.loads(
        json.dumps(ignore_checks(babysit_snapshot(read), i.work.ignored_checks))
    )
    discussion = (
        json.loads(
            json.dumps(compact(gate(f"https://github.com/{target.slug}/pull/{number}")))
        )
        if read.pull_request.is_open
        else {}
    )
    return PullRequestPolled(
        target, number, projected, i.github.login(), bot_logins(read), discussion
    )


def _decide(
    i: DecideInput,
    author: str | None,
    copilot: Callable[[PullRequestPolled], bool],
) -> NextAction:
    polled = i.polled
    if not polled.is_open:
        return PullRequestClosed(polled.head, bool(polled.snapshot["pr"].get("merged")))
    if _unsupported(polled, author):
        return Unsupported(_UNSUPPORTED)
    pr = polled.snapshot["pr"]
    # Drafts are only watched; work starts once the PR is marked ready.
    if pr.get("draft"):
        return DraftWaiting(polled.head)
    if pr.get("mergeable") is None:
        return ReadinessCheck(polled.head, False)
    if pr.get("mergeable") is False:
        base = pr["base"]
        return MergeConflict(polled.head, base["ref"], base["sha"])
    if polled.discussion.get("action") == "stop":
        return Unsupported(f"Address gate stopped: {polled.discussion.get('reason')}")
    pending = _discussion(i)
    if pending is not None:
        return pending
    if ci_failed(polled.snapshot):
        return CiFailing(polled.head)
    if copilot(polled) and polled.head not in {event.head for event in i.requested}:
        return CopilotDue(polled.head)
    return ReadinessCheck(polled.head, _ready(polled.snapshot))


def _unsupported(polled: PullRequestPolled, author: str | None) -> bool:
    """The pull request is by someone other than the expected author, or its head lives in another repository."""
    pr = polled.snapshot["pr"]
    head_repository = (pr["head"].get("repo") or {}).get("full_name", "")
    return (
        pr["user"]["login"].lower() != (author or polled.login).lower()
        or head_repository.lower() != polled.target.slug.lower()
    )


def _discussion(i: DecideInput) -> DiscussionPending | None:
    """The review-thread work the address gate reports, unless an earlier address run already left the discussion in exactly this state; None when there is none."""
    discussion = i.polled.discussion
    if discussion.get("action") != "proceed":
        return None
    token = discussion_token(discussion)
    if any(token in event.tokens for event in i.addressed):
        return None
    return DiscussionPending(
        i.polled.head, ci_failed(i.polled.snapshot), token, discussion
    )


def _ready(snapshot: dict[str, Any]) -> bool:
    """CI is green, no Copilot review is pending, and GitHub reports the non-draft pull request clean (or unstable only through ignored checks) and not waiting on a review."""
    pr = snapshot["pr"]
    copilot_pending = any(
        is_copilot(user["login"]) for user in pr.get("requested_reviewers", [])
    )
    decision = snapshot.get("review", {}).get("reviewDecision")
    return (
        ci_state(snapshot)["green"]
        and not copilot_pending
        and not pr["draft"]
        and pr.get("mergeable") is True
        and (
            pr.get("mergeable_state") == "clean"
            or (
                pr.get("mergeable_state") == "unstable"
                and bool(snapshot.get("ignored"))
            )
        )
        and decision not in _UNREADY_DECISIONS
    )


def _blocked(
    i: BlockInput, fingerprint: Callable[[PullRequestPolled], str]
) -> PrBlocked:
    polled = _reread(i) or (i.polled[-1] if i.polled else None)
    detail = i.unsupported[-1].reason if i.unsupported else ""
    return PrBlocked(fingerprint(polled) if polled is not None else "", detail)


def _reread(i: BlockInput) -> PullRequestPolled | None:
    """A fresh read, so our own replies are in the fingerprint and cannot retrigger the blocked pull request; None when GitHub cannot be read."""
    try:
        return _poll(SnapshotInput(i.work, i.github))
    except CommandError, OSError, ValueError:
        return None


def _blocked_attributes(blocked: PrBlocked, reason: str | None) -> dict[str, str]:
    return {
        "factory.pr.fingerprint": blocked.fingerprint,
        "factory.pr.reason": reason or blocked.detail,
    }

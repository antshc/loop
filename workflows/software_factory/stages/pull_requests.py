"""The pull request Stages: validate the checkout, take a snapshot, decide the next action, publish a batch and block the pull request."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from loop import AgentWorkspace, Evidence, Fail, History, Hold, Pass, Stage, Verdict
from loop.shared.git import GitClient
from loop.shared.github.account import GitHub
from loop.shared.github.pull_requests import PullRequests
from loop.shared.github.pull_requests.babysit import (
    babysit_snapshot,
    bot_logins,
    ci_failed,
    ci_state,
    is_copilot,
    unhandled_items,
)
from loop.shared.github.pull_requests.model import PullRequestSnapshot
from loop.shared.github.repo import CommandError

from workflows.software_factory.events.ci import CiFixed
from workflows.software_factory.events.feedback import (
    FeedbackBatch,
    FeedbackHandled,
    IssueResolved,
    IssueUnclear,
    TrustPolicy,
)
from workflows.software_factory.events.pull_requests import (
    CiFailing,
    CopilotDue,
    CopilotRequested,
    NextAction,
    OriginValidated,
    PrBlocked,
    PullRequestClosed,
    PullRequestPolled,
    PullRequestWork,
    ReadinessCheck,
    Unsupported,
)

_POSTED, _MISSING, _SUPERSEDED = "posted", "missing", "superseded"
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
    handled: History[FeedbackHandled]
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
    batch: FeedbackBatch | CiFixed
    polled: PullRequestPolled
    resolved: History[IssueResolved]
    unclear: History[IssueUnclear]
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
    trust: TrustPolicy,
    author: str | None,
    copilot: Callable[[PullRequestPolled], bool],
) -> Stage[DecideInput, NextAction, NextAction]:
    """Pure: closed, then author or fork mismatch, then trusted feedback not handled in earlier runs, then failed CI, then a Copilot review `copilot` wants for a head not requested yet, otherwise a readiness check; `author` defaults to the acting account."""
    return (
        Stage("decide-next", DecideInput, produces=NextAction)
        .action(lambda i: _decide(i, trust, author, copilot))
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
    """Pushes the worktree head without force when it is ahead, confirms the remote head equals it, then replies once to every feedback item (thread replies in their thread, others as Comments), reconciling each reply so a crash mid-loop never posts twice."""
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
    """The publication a crashed attempt completed: the remote head is the worktree head and every reply stands."""
    pulls, number, local = _pulls(i), i.polled.number, i.git.head(i.worktree.path).sha
    pull = pulls.pull_request(number)
    if not pull.is_open or pull.head_ref != i.polled.branch or pull.head_sha != local:
        return None
    snapshot, login = pulls.snapshot(number), i.github.login()
    states = {
        key: _reply_state(snapshot, _item(i, key), _text(_item(i, key), body), login)
        for key, body in _drafts(i).items()
    }
    if _MISSING in states.values():
        return None
    return FeedbackHandled(
        tuple(key for key, state in states.items() if state == _POSTED), local
    )


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
        return _Refused("PR head moved; publication stopped before replying")
    if local != i.batch.head and pull.head_sha != local:
        i.git.publish(path, i.polled.branch)
    if pulls.pull_request(number).head_sha != local:
        return _Refused("Published SHA could not be confirmed")
    return FeedbackHandled(_reply_once(i, pulls), local)


def _reply_once(i: PublishInput, pulls: PullRequests) -> tuple[str, ...]:
    """Posts each drafted reply that is not on the pull request yet; returns the keys that now have their reply."""
    login, handled = i.github.login(), []
    for key, body in _drafts(i).items():
        item = _item(i, key)
        text = _text(item, body)
        state = _reply_state(pulls.snapshot(i.polled.number), item, text, login)
        if state == _MISSING:
            _post(pulls, i.polled.number, item, text)
        if state != _SUPERSEDED:
            handled.append(key)
    return tuple(handled)


def _post(pulls: PullRequests, number: int, item: dict[str, Any], text: str) -> None:
    """Replies in the review thread the item came from, or on the pull request conversation."""
    if item["kind"] == "thread":
        pulls.reply_to_thread(item["thread_id"], text)
    else:
        pulls.comment(number, text)


def _reply_state(
    snapshot: PullRequestSnapshot, item: dict[str, Any], text: str, login: str
) -> str:
    """Whether `text` already stands as the reply to `item`, is still owed, or was overtaken by a newer comment in its thread."""
    if item["kind"] != "thread":
        return (
            _POSTED
            if any(c.author == login and c.body == text for c in snapshot.comments)
            else _MISSING
        )
    thread = next(
        (
            candidate
            for candidate in snapshot.threads
            if candidate.id == item["thread_id"]
        ),
        None,
    )
    if thread is None or thread.resolved:
        return _POSTED
    source = item["key"].rsplit(":", 1)[-1]
    index = next(
        (n for n, comment in enumerate(thread.comments) if comment.id == source),
        len(thread.comments),
    )
    if any(
        comment.author == login and comment.body == text
        for comment in thread.comments[index + 1 :]
    ):
        return _POSTED
    return (
        _SUPERSEDED
        if thread.comments and thread.comments[-1].id != source
        else _MISSING
    )


def _drafts(i: PublishInput) -> dict[str, str]:
    """The reply drafted for each feedback key of the batch, in batch order."""
    token = i.batch.token
    drafted = {
        draft.key: draft.body
        for event in (*i.resolved, *i.unclear)
        if event.batch == token
        for draft in event.replies
    }
    return {key: drafted[key] for key in i.batch.keys if key in drafted}


def _item(i: PublishInput, key: str) -> dict[str, Any]:
    return next(item for item in i.batch.items if item["key"] == key)


def _text(item: dict[str, Any], body: str) -> str:
    return (
        body if item["kind"] == "thread" else f"In response to {item['url']}:\n\n{body}"
    )


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
    projected = json.loads(json.dumps(babysit_snapshot(read)))
    return PullRequestPolled(
        target, number, projected, i.github.login(), bot_logins(read)
    )


def _decide(
    i: DecideInput,
    trust: TrustPolicy,
    author: str | None,
    copilot: Callable[[PullRequestPolled], bool],
) -> NextAction:
    polled = i.polled
    if not polled.is_open:
        return PullRequestClosed(polled.head, bool(polled.snapshot["pr"].get("merged")))
    if _unsupported(polled, author):
        return Unsupported(_UNSUPPORTED)
    batch = _feedback(i, trust)
    if batch is not None:
        return batch
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


def _feedback(i: DecideInput, trust: TrustPolicy) -> FeedbackBatch | None:
    """The trusted feedback no earlier batch replied to, with the CI state of the head; None when there is none."""
    handled = {key for event in i.handled for key in event.keys}
    items = unhandled_items(
        i.polled.snapshot, i.polled.login, trust, handled, frozenset(i.polled.bots)
    )
    return (
        FeedbackBatch(i.polled.head, ci_failed(i.polled.snapshot), tuple(items))
        if items
        else None
    )


def _ready(snapshot: dict[str, Any]) -> bool:
    """CI is green, no Copilot review is pending, and GitHub reports the non-draft pull request clean and not waiting on a review."""
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
        and pr.get("mergeable_state") == "clean"
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

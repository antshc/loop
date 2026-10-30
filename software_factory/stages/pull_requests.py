"""The pull request publication Stage: push a batch's commits without force, confirm the remote head, then reply once to every feedback item."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from loop import AgentWorkspace

from software_factory.events.feedback import FeedbackBatch, FeedbackHandled, IssueResolved, IssueUnclear
from software_factory.events.pull_requests import PullRequestPolled
from software_factory.runtime import Evidence, Fail, History, Pass, Stage, Verdict
from software_factory.shared.git import GitClient
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.pull_requests import PullRequests
from software_factory.shared.github.pull_requests.model import PullRequestSnapshot

_POSTED, _MISSING, _SUPERSEDED = "posted", "missing", "superseded"


@dataclass(frozen=True)
class PublishInput:
    worktree: AgentWorkspace
    batch: FeedbackBatch
    polled: PullRequestPolled
    resolved: History[IssueResolved]
    unclear: History[IssueUnclear]
    git: GitClient
    github: GitHub


@dataclass(frozen=True)
class _Refused:
    reason: str


def publish_batch() -> Stage[PublishInput, FeedbackHandled | _Refused, FeedbackHandled]:
    """Pushes the worktree head without force when it is ahead, confirms the remote head equals it, then replies once to every feedback item (thread replies in their thread, others as Comments), reconciling each reply so a crash mid-loop never posts twice."""
    return (
        Stage("publish-batch", PublishInput, produces=FeedbackHandled)
        .effect(lambda i: _published(i) or _publish(i), reconcile=_published)
        .gate(_accepted)
    )


def _accepted(_: PublishInput, reply: FeedbackHandled | _Refused, __: Evidence) -> Verdict[FeedbackHandled]:
    return Fail(reply.reason) if isinstance(reply, _Refused) else Pass(reply)


def _published(i: PublishInput) -> FeedbackHandled | None:
    """The publication a crashed attempt completed: the remote head is the worktree head and every reply stands."""
    pulls, number, local = _pulls(i), i.polled.number, i.git.head(i.worktree.path).sha
    pull = pulls.pull_request(number)
    if not pull.is_open or pull.head_ref != i.polled.branch or pull.head_sha != local:
        return None
    snapshot, login = pulls.snapshot(number), i.github.login()
    states = {key: _reply_state(snapshot, _item(i, key), _text(_item(i, key), body), login) for key, body in _drafts(i).items()}
    if _MISSING in states.values():
        return None
    return FeedbackHandled(tuple(key for key, state in states.items() if state == _POSTED), local)


def _publish(i: PublishInput) -> FeedbackHandled | _Refused:
    pulls, number, path = _pulls(i), i.polled.number, i.worktree.path
    local, pull = i.git.head(path).sha, pulls.pull_request(number)
    if not pull.is_open:
        return _Refused("PR closed during publication; inspect retained work before retrying")
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


def _reply_state(snapshot: PullRequestSnapshot, item: dict[str, Any], text: str, login: str) -> str:
    """Whether `text` already stands as the reply to `item`, is still owed, or was overtaken by a newer comment in its thread."""
    if item["kind"] != "thread":
        return _POSTED if any(c.author == login and c.body == text for c in snapshot.comments) else _MISSING
    thread = next((candidate for candidate in snapshot.threads if candidate.id == item["thread_id"]), None)
    if thread is None or thread.resolved:
        return _POSTED
    source = item["key"].rsplit(":", 1)[-1]
    index = next((n for n, comment in enumerate(thread.comments) if comment.id == source), len(thread.comments))
    if any(comment.author == login and comment.body == text for comment in thread.comments[index + 1:]):
        return _POSTED
    return _SUPERSEDED if thread.comments and thread.comments[-1].id != source else _MISSING


def _drafts(i: PublishInput) -> dict[str, str]:
    """The reply drafted for each feedback key of the batch, in batch order."""
    token = i.batch.token
    drafted = {draft.key: draft.body for event in (*i.resolved, *i.unclear) if event.batch == token for draft in event.replies}
    return {key: drafted[key] for key in i.batch.keys if key in drafted}


def _item(i: PublishInput, key: str) -> dict[str, Any]:
    return next(item for item in i.batch.items if item["key"] == key)


def _text(item: dict[str, Any], body: str) -> str:
    return body if item["kind"] == "thread" else f"In response to {item['url']}:\n\n{body}"


def _pulls(i: PublishInput) -> PullRequests:
    return i.github.pull_requests(i.polled.target.slug)

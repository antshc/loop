"""The babysit view of a pull request: the snapshot dictionary babysit's fingerprints and agent envelopes are built on, the review feedback a trust policy accepts, and the CI state of the head."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Protocol

from ..issues.issue import Comment
from .model import CheckKind, PullRequestReview, PullRequestSnapshot, ReviewComment, ReviewThread, ReviewVerdict

FIX_MARKER = "fix!:"
FAILED = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
COPILOT = {"copilot", "copilot-pull-request-reviewer"}


class Trust(Protocol):
    """Whose feedback is accepted: author associations and review-automation logins."""

    associations: frozenset[str]
    bots: frozenset[str]


def babysit_snapshot(snapshot: PullRequestSnapshot) -> dict[str, Any]:
    """The snapshot dictionary of a pull request; a closed one has no discussion, checks, or review summary."""
    pull = snapshot.pull_request
    projected: dict[str, Any] = {"pr": dict(pull.payload), "threads": [], "other": [], "runs": [], "checks": [], "statuses": []}
    if not pull.is_open:
        return projected
    projected["review"] = {"mergeStateStatus": snapshot.merge_state_status.value.upper(), "reviewDecision": snapshot.review_decision.value}
    projected["threads"] = [_thread(thread) for thread in snapshot.threads]
    projected["other"] = [*_comments(snapshot.comments), *_reviews(snapshot.reviews)]
    current = snapshot.current_checks
    projected["checks"] = [check.payload for check in current if check.kind is CheckKind.CHECK_RUN]
    projected["statuses"] = [check.payload for check in current if check.kind is CheckKind.STATUS]
    projected["runs"] = [check.payload for check in current if check.kind is CheckKind.WORKFLOW_RUN]
    return projected


def bot_logins(snapshot: PullRequestSnapshot) -> tuple[str, ...]:
    """The review-thread authors whose account is a bot; kept beside the snapshot dictionary, which fingerprints and agent envelopes are built on."""
    return tuple(sorted({comment.author for thread in snapshot.threads for comment in thread.comments if comment.author_is_bot and comment.author}))


def digest(value: object) -> str:
    """A stable key for a piece of remote work."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def trusted(comment: dict[str, Any], login: str, trust: Trust, bots: frozenset[str] = frozenset()) -> bool:
    """Accepts the acting account, authors with a trusted association and known review automation whose account is a bot: a REST `user.type` of Bot, or a login in `bots`."""
    author = comment.get("author")
    name = (author.get("login") if isinstance(author, dict) else author) or ""
    association = comment.get("authorAssociation", comment.get("author_association"))
    is_bot = (isinstance(author, dict) and author.get("type") == "Bot") or name in bots
    return name == login or association in trust.associations or (is_bot and name.lower().removesuffix("[bot]") in trust.bots)


def pending_threads(
    threads: list[dict[str, Any]], login: str, trust: Trust, bots: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """The open review threads whose first comment is trusted and whose latest comment is not the account's own answer; `bots` are the logins that are bot accounts."""
    pending = []
    for thread in threads:
        comments = thread["comments"]
        if thread["isResolved"] or not comments or not trusted(comments[0], login, trust, bots):
            continue
        latest_comment = comments[-1]
        own = (latest_comment.get("author") or {}).get("login") == login
        self_note = len(comments) == 1 and own
        if own and not self_note and FIX_MARKER not in latest_comment["body"].lower():
            continue
        last_reply = max((index for index, comment in enumerate(comments[:-1]) if _is_by(comment, login)), default=-1)
        pending.append({
            "key": f"thread:{thread['id']}:{latest_comment['id']}", "kind": "thread", "thread_id": thread["id"],
            "path": thread["path"], "line": thread.get("line") or thread.get("originalLine"),
            "outdated": thread.get("isOutdated", False), "comments": comments[last_reply + 1:],
            "previous_reply": comments[last_reply] if last_reply >= 0 else None,
        })
    return pending


def pending_items(snapshot: dict[str, Any], login: str, trust: Trust, bots: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Every trusted, unanswered feedback item of the snapshot: review threads first, then conversation Comments and reviews; `bots` are the thread authors that are bot accounts."""
    pending = pending_threads(snapshot["threads"], login, trust, bots)
    for item in snapshot["other"]:
        if not trusted(item, login, trust) or (_is_by(item, login) and FIX_MARKER not in item["body"].lower()):
            continue
        pending.append({**item, "key": f"{item['kind']}:{item['id']}:{digest(item['body'])}"})
    return pending


def unhandled_items(
    snapshot: dict[str, Any], login: str, trust: Trust, handled: set[str], bots: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """The pending feedback items whose key no earlier batch replied to, each marked `pending` as persisted before."""
    return [{**item, "status": "pending"} for item in pending_items(snapshot, login, trust, bots) if item["key"] not in handled]


def latest(items: list[dict[str, Any]], key: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    """The latest occurrence of each check or workflow."""
    result: dict[Any, dict[str, Any]] = {}
    for item in sorted(items, key=lambda value: value["id"], reverse=True):
        result.setdefault(key(item), item)
    return list(result.values())


def ci_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Separates pending, failed and green signals on the current head; `runs` are the failed workflow runs."""
    checks = latest(snapshot["checks"], lambda c: (c["name"], (c.get("app") or {}).get("id")))
    statuses = latest(snapshot["statuses"], lambda s: s["context"])
    runs = latest(snapshot["runs"], lambda r: (r["workflow_id"], r.get("event")))
    pending = (
        any(check["status"] != "completed" for check in checks)
        or any(status["state"] == "pending" for status in statuses)
        or any(run["status"] != "completed" for run in runs)
    )
    failed_runs = [run for run in runs if run.get("conclusion") in FAILED and run["status"] == "completed"]
    failed = (
        bool(failed_runs)
        or any(check.get("conclusion") in FAILED for check in checks)
        or any(status["state"] in {"failure", "error"} for status in statuses)
    )
    return {"pending": pending, "failed": failed, "runs": failed_runs, "green": not pending and not failed}


def ci_failed(snapshot: dict[str, Any]) -> bool:
    """Whether any latest check, status or workflow run on the pull request head has failed."""
    return bool(ci_state(snapshot)["failed"])


def is_copilot(login: str) -> bool:
    """Whether `login` is the Copilot reviewer."""
    return login.lower().removesuffix("[bot]") in COPILOT


def _is_by(comment: dict[str, Any], login: str) -> bool:
    return (comment.get("author") or {}).get("login") == login


def _thread(thread: ReviewThread) -> dict[str, Any]:
    return {"id": thread.id, "isResolved": thread.resolved, "isOutdated": thread.outdated, "path": thread.path,
            "line": thread.line, "originalLine": thread.original_line,
            "comments": [_review_comment(comment) for comment in thread.comments]}


def _review_comment(comment: ReviewComment) -> dict[str, Any]:
    return {"id": comment.id, "databaseId": comment.database_id, "body": comment.body, "createdAt": comment.created_at,
            "diffHunk": comment.diff_hunk, "author": None if comment.author is None else {"login": comment.author},
            "authorAssociation": comment.author_association,
            "pullRequestReview": None if comment.review_state is None else {"state": comment.review_state.value},
            "url": comment.url}


def _comments(comments: tuple[Comment, ...]) -> list[dict[str, Any]]:
    return [_other("comment", comment.ref.id, comment.body, comment.created_at, comment.payload)
            for comment in comments if comment.body.strip()]


def _reviews(reviews: tuple[PullRequestReview, ...]) -> list[dict[str, Any]]:
    return [_other("review", review.id, review.body, review.submitted_at or review.payload.get("created_at"), review.payload)
            for review in reviews if review.verdict is not ReviewVerdict.PENDING and review.body.strip()]


def _other(kind: str, id: int, body: str, created_at: str | None, payload: Any) -> dict[str, Any]:
    return {"kind": kind, "id": str(id), "body": body, "author": payload["user"],
            "author_association": payload.get("author_association"), "created_at": created_at,
            "updated_at": payload.get("updated_at"), "url": payload["html_url"]}

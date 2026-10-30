"""The feedback Stage: the trusted, still unanswered review feedback of a pull request, selected by a trust policy."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from software_factory.events.feedback import FeedbackBatch, FeedbackHandled, TrustPolicy
from software_factory.events.pull_requests import PullRequestPolled
from software_factory.runtime import Evidence, Hold, History, Pass, Stage, Verdict
from software_factory.shared.github.account import GitHub

FIX_MARKER = "fix!:"
FAILED = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}


@dataclass(frozen=True)
class SelectInput:
    polled: PullRequestPolled
    handled: History[FeedbackHandled]
    github: GitHub


def select(*, trust: TrustPolicy) -> Stage[SelectInput, FeedbackBatch, FeedbackBatch]:
    """The pull request's pending feedback from authors `trust` accepts, minus what earlier batches replied to; holds when there is none."""
    return (
        Stage("select-feedback", SelectInput, produces=FeedbackBatch)
        .action(lambda i: _batch(i, trust))
        .gate(_selected)
    )


def digest(value: object) -> str:
    """A stable key for a piece of remote work."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def trusted(comment: dict[str, Any], login: str, trust: TrustPolicy) -> bool:
    """Accepts the acting account, authors with a trusted association and known review automation."""
    author = comment.get("author")
    name = (author.get("login") if isinstance(author, dict) else author) or ""
    association = comment.get("authorAssociation", comment.get("author_association"))
    return name == login or association in trust.associations or name.lower().removesuffix("[bot]") in trust.bots


def pending_threads(threads: list[dict[str, Any]], login: str, trust: TrustPolicy) -> list[dict[str, Any]]:
    """The open review threads whose first comment is trusted and whose latest comment is not the account's own answer."""
    pending = []
    for thread in threads:
        comments = thread["comments"]
        if thread["isResolved"] or not comments or not trusted(comments[0], login, trust):
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


def pending_items(snapshot: dict[str, Any], login: str, trust: TrustPolicy) -> list[dict[str, Any]]:
    """Every trusted, unanswered feedback item of the snapshot: review threads first, then conversation Comments and reviews."""
    pending = pending_threads(snapshot["threads"], login, trust)
    for item in snapshot["other"]:
        if not trusted(item, login, trust) or (_is_by(item, login) and FIX_MARKER not in item["body"].lower()):
            continue
        pending.append({**item, "key": f"{item['kind']}:{item['id']}:{digest(item['body'])}"})
    return pending


def latest(items: list[dict[str, Any]], key: Callable[[dict[str, Any]], Any]) -> list[dict[str, Any]]:
    """The latest occurrence of each check or workflow."""
    result: dict[Any, dict[str, Any]] = {}
    for item in sorted(items, key=lambda value: value["id"], reverse=True):
        result.setdefault(key(item), item)
    return list(result.values())


def ci_failed(snapshot: dict[str, Any]) -> bool:
    """Whether any latest check, status or workflow run on the pull request head has failed."""
    checks = latest(snapshot["checks"], lambda c: (c["name"], (c.get("app") or {}).get("id")))
    statuses = latest(snapshot["statuses"], lambda s: s["context"])
    runs = latest(snapshot["runs"], lambda r: (r["workflow_id"], r.get("event")))
    failed_runs = [run for run in runs if run.get("conclusion") in FAILED and run["status"] == "completed"]
    return (
        bool(failed_runs)
        or any(check.get("conclusion") in FAILED for check in checks)
        or any(status["state"] in {"failure", "error"} for status in statuses)
    )


def _is_by(comment: dict[str, Any], login: str) -> bool:
    return (comment.get("author") or {}).get("login") == login


def _batch(i: SelectInput, trust: TrustPolicy) -> FeedbackBatch:
    snapshot = i.polled.snapshot
    handled = {key for event in i.handled for key in event.keys}
    pending = pending_items(snapshot, i.github.login(), trust)
    items = tuple({**item, "status": "pending"} for item in pending if item["key"] not in handled)
    return FeedbackBatch(i.polled.head, ci_failed(snapshot), items)


def _selected(_: SelectInput, batch: FeedbackBatch, __: Evidence) -> Verdict[FeedbackBatch]:
    return Pass(batch) if batch.items else Hold("no pending feedback")

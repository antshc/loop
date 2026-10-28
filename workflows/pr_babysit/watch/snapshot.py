"""Projects a typed pull request snapshot back to the dictionary shape babysit's fingerprints, persisted state, and agent envelopes were built on."""

from __future__ import annotations

from typing import Any

from workflows.shared.github.issues.issue import Comment
from workflows.shared.github.pull_requests.model import (
    CheckKind,
    PullRequestReview,
    PullRequestSnapshot,
    ReviewComment,
    ReviewThread,
    ReviewVerdict,
)


def babysit_snapshot(snapshot: PullRequestSnapshot) -> dict[str, Any]:
    """The snapshot dictionary of a pull request; a closed one has no discussion, checks, or review summary."""
    pull = snapshot.pull_request
    projected: dict[str, Any] = {"pr": dict(pull.payload), "threads": [], "other": [], "runs": [], "checks": [], "statuses": []}
    if not pull.is_open:
        return projected
    projected["review"] = {"mergeStateStatus": snapshot.merge_state_status, "reviewDecision": snapshot.review_decision}
    projected["threads"] = [_thread(thread) for thread in snapshot.threads]
    projected["other"] = [*_comments(snapshot.comments), *_reviews(snapshot.reviews)]
    current = snapshot.current_checks
    projected["checks"] = [check.payload for check in current if check.kind is CheckKind.CHECK_RUN]
    projected["statuses"] = [check.payload for check in current if check.kind is CheckKind.STATUS]
    projected["runs"] = [check.payload for check in current if check.kind is CheckKind.WORKFLOW_RUN]
    return projected


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

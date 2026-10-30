"""Representative pull requests whose babysit fingerprints and agent envelopes were captured, as `golden.json`, before the migration to the factory runtime."""

from __future__ import annotations

from pathlib import Path

from software_factory.shared.github.account import GitHub
from software_factory.shared.github.pull_requests import PullRequests
from tests.shared.fakes import (
    FakeGhCli,
    FakePullRequestHost,
    check_run,
    comment_node,
    commit_status,
    issue_comment,
    review,
    thread_node,
    workflow_run,
)

GOLDEN = Path(__file__).with_name("golden.json")

OPEN, CLOSED, GREEN = 1, 2, 3

_HEAD = "sha-head"


def build_host() -> FakePullRequestHost:
    """Three pull requests: one open with every kind of feedback and CI signal, one merged, one green and quiet."""
    host = FakePullRequestHost(thread_page_size=2, comment_page_size=2)
    _open_with_feedback(host.add(OPEN, head_sha=_HEAD))
    host.add(CLOSED, head_sha="sha-closed").close(merged=True)
    _green(host.add(GREEN, head_sha="sha-green"))
    return host


def _open_with_feedback(pull) -> None:
    pull.requested_reviewers = ["copilot-pull-request-reviewer[bot]"]
    pull.review_decision = "REVIEW_REQUIRED"
    pull.threads = [
        thread_node([
            comment_node("c1", "reviewer", "Handle the empty cart"),
            comment_node("c2", "me", "Fixed!"),
            comment_node("c3", "reviewer", "Also handle null products"),
            comment_node("c4", "me", "draft reply", review_state="PENDING"),
        ], id="t1"),
        thread_node([comment_node("c5", "reviewer", "Already done")], id="t2", resolved=True),
        thread_node([comment_node("c6", "copilot-pull-request-reviewer[bot]", "Rename this", association="NONE")],
                    id="t3", outdated=True, line=None, original_line=9),
        thread_node([comment_node("c7", "outsider", "Drive-by", association="NONE")], id="t4"),
    ]
    pull.comments = [
        issue_comment(501, "reviewer", "Please add docs"),
        issue_comment(502, "reviewer", "  "),
        issue_comment(503, "me", "In response to the docs request"),
    ]
    pull.reviews = [
        review(601, "reviewer", "LGTM with nits", state="APPROVED", commit=_HEAD),
        review(602, "reviewer", "draft review", state="PENDING"),
        review(603, "reviewer", "", state="COMMENTED"),
        review(604, "outsider", "Needs work", state="CHANGES_REQUESTED", association="NONE"),
    ]
    pull.check_runs = [
        check_run(1, "test", conclusion="failure"),
        check_run(2, "test", conclusion="success", commit=_HEAD),
        check_run(3, "lint", status="in_progress", conclusion=None),
    ]
    pull.statuses = [commit_status(1, "ci/slow", state="pending"), commit_status(2, "ci/fast")]
    pull.workflow_runs = [
        workflow_run(10, _HEAD, conclusion="failure"),
        workflow_run(11, _HEAD, conclusion="failure", workflow_id=2, attempt=2),
        workflow_run(12, "sha-older", conclusion="failure", workflow_id=3),
    ]


def _green(pull) -> None:
    pull.check_runs = [check_run(1, "test", commit="sha-green")]
    pull.statuses = [commit_status(1, "ci/fast")]
    pull.workflow_runs = [workflow_run(10, "sha-green")]


def pulls_over(host: FakePullRequestHost) -> PullRequests:
    """The shared pull request client reading `host` through the `gh` boundary."""
    return GitHub(FakeGhCli(pulls=host)).pull_requests("a/b")

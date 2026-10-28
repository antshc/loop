from __future__ import annotations

import pytest

from workflows.shared.github.account import GitHub
from workflows.shared.github.identity import CommentRef, IssueIdentity, IssueRef, RepositoryId
from workflows.shared.github.issues.issue import Comment
from workflows.shared.github.pull_requests import PullRequests
from workflows.shared.github.pull_requests.model import (
    CheckKind,
    Mergeability,
    MergeState,
    PullRequestState,
    ReviewComment,
    ReviewDecision,
    ReviewVerdict,
)
from workflows.shared.process import CommandError
from workflows.tests.shared.fakes import (
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


def _read(host: FakePullRequestHost) -> tuple[PullRequests, FakeGhCli]:
    gh = FakeGhCli(pulls=host)
    return GitHub(gh).pull_requests("a/b"), gh


def test_a_pull_request_read_carries_its_identity_branches_author_and_merge_state() -> None:
    host = FakePullRequestHost()
    pull = host.add(7, head_ref="feat", head_sha="abc", author="alice")
    pull.title, pull.body, pull.draft = "Add login", "Closes the login Ticket", True
    pull.mergeable_state, pull.requested_reviewers = "blocked", ["bob"]

    read = _read(host)[0].pull_request(7)

    assert read.ref == IssueRef(RepositoryId("a", "b"), 7) and read.number == 7
    assert read.identity == IssueIdentity(9007, "PR_7")
    assert (read.title, read.body, read.url) == ("Add login", "Closes the login Ticket", "https://github.com/a/b/pull/7")
    assert (read.state, read.draft, read.merged, read.is_open) == (PullRequestState.OPEN, True, False, True)
    assert (read.base_ref, read.head_ref, read.head_sha, read.head_repository) == ("main", "feat", "abc", "a/b")
    assert (read.author, read.requested_reviewers) == ("alice", ("bob",))
    assert (read.mergeability, read.merge_state) == (Mergeability.MERGEABLE, MergeState.BLOCKED)


def test_a_merged_pull_request_is_closed_and_merged() -> None:
    host = FakePullRequestHost()
    host.add(7).close(merged=True)

    read = _read(host)[0].pull_request(7)

    assert (read.state, read.merged, read.is_open) == (PullRequestState.CLOSED, True, False)


def test_unknown_mergeability_is_neither_mergeable_nor_conflicting() -> None:
    host = FakePullRequestHost()
    pull = host.add(7)
    pull.mergeable, pull.mergeable_state = None, "unknown"

    read = _read(host)[0].pull_request(7)

    assert read.mergeability is Mergeability.UNKNOWN and read.merge_state is MergeState.UNKNOWN
    assert not read.mergeable and not read.conflicting


def test_a_conflicting_pull_request_is_not_mergeable() -> None:
    host = FakePullRequestHost()
    pull = host.add(7)
    pull.mergeable, pull.mergeable_state = False, "dirty"

    read = _read(host)[0].pull_request(7)

    assert read.conflicting and not read.mergeable and read.merge_state is MergeState.DIRTY


def test_a_listing_reports_no_mergeability_so_it_is_unknown() -> None:
    host = FakePullRequestHost()
    host.add(1, author="Me")
    host.add(2, author="other")
    host.add(3, author="me").draft = True
    host.add(4, author="me").close()

    listed = _read(host)[0].open_by("me")

    assert [pull.number for pull in listed] == [1, 3]
    assert {pull.mergeability for pull in listed} == {Mergeability.UNKNOWN}
    assert listed[1].draft


def test_every_review_thread_and_every_comment_is_returned_across_pages() -> None:
    host = FakePullRequestHost(thread_page_size=2, comment_page_size=2)
    host.add(1).threads = [
        thread_node([comment_node(f"c{thread}-{index}", "reviewer", f"Comment {index}") for index in range(5)],
                    id=f"t{thread}", resolved=thread == 2, outdated=thread == 3, line=None if thread == 3 else thread)
        for thread in range(5)
    ]
    pulls, gh = _read(host)

    threads = pulls.threads(1)

    assert [thread.id for thread in threads] == ["t0", "t1", "t2", "t3", "t4"]
    assert all([comment.id for comment in thread.comments] == [f"c{thread.id[1:]}-{index}" for index in range(5)] for thread in threads)
    assert [(thread.resolved, thread.outdated) for thread in threads] == [
        (False, False), (False, False), (True, False), (False, True), (False, False)]
    assert threads[3].line is None and threads[4].line == 4
    assert "cursor=" not in " ".join(gh.calls[0])


def test_comments_of_a_review_its_author_has_not_submitted_are_not_returned() -> None:
    host = FakePullRequestHost()
    host.add(1).threads = [thread_node([
        comment_node("c1", "reviewer", "Visible"),
        comment_node("c2", "me", "Draft reply", review_state="PENDING"),
    ])]

    (thread,) = _read(host)[0].threads(1)

    assert [comment.id for comment in thread.comments] == ["c1"]


def test_a_review_comment_keeps_its_author_association_and_review_state() -> None:
    host = FakePullRequestHost()
    host.add(1).threads = [thread_node([
        comment_node("c1", "reviewer", "Fix", association="MEMBER", review_state="CHANGES_REQUESTED"),
        comment_node("c2", None, "From a deleted account", association="NONE", review_state=None),
    ])]

    (thread,) = _read(host)[0].threads(1)

    first, second = thread.comments
    assert isinstance(first, ReviewComment)
    assert (first.author, first.author_association, first.review_state) == ("reviewer", "MEMBER", ReviewVerdict.CHANGES_REQUESTED)
    assert (second.author, second.review_state) == (None, None)


def test_each_review_has_its_reviewer_verdict_and_the_commit_it_was_submitted_against() -> None:
    host = FakePullRequestHost()
    pull = host.add(1, head_sha="head")
    pull.reviews = [
        review(601, "alice", "LGTM", state="APPROVED", commit="older"),
        review(602, "bob", "Needs work", state="CHANGES_REQUESTED", commit="head"),
        review(603, "carol", "", state="DISMISSED", commit="older"),
    ]

    reviews = _read(host)[0].snapshot(1).reviews

    assert [(r.id, r.reviewer, r.verdict, r.commit) for r in reviews] == [
        (601, "alice", ReviewVerdict.APPROVED, "older"),
        (602, "bob", ReviewVerdict.CHANGES_REQUESTED, "head"),
        (603, "carol", ReviewVerdict.DISMISSED, "older"),
    ]


def test_conversation_comments_are_the_comment_type_issues_use_and_never_review_comments() -> None:
    host = FakePullRequestHost()
    pull = host.add(1)
    pull.comments = [issue_comment(501, "alice", "General remark")]
    pull.threads = [thread_node([comment_node("c1", "reviewer", "Line remark")])]
    gh = FakeGhCli(pulls=host)
    account = GitHub(gh)

    snapshot = account.pull_requests("a/b").snapshot(1)
    issue_comment_read = account.issues("a/b").issue(10).comments[0]

    (conversation,) = snapshot.comments
    assert type(conversation) is Comment is type(issue_comment_read)
    assert (conversation.ref, conversation.author, conversation.body) == (
        CommentRef(IssueRef(RepositoryId("a", "b"), 1), 501), "alice", "General remark")
    (thread,) = snapshot.threads
    assert all(isinstance(comment, ReviewComment) and not isinstance(comment, Comment) for comment in thread.comments)
    assert [comment.body for comment in thread.comments] == ["Line remark"]


def test_every_list_of_a_snapshot_is_followed_to_its_last_page() -> None:
    host = FakePullRequestHost(page_size=2)
    pull = host.add(1, head_sha="head")
    pull.comments = [issue_comment(500 + index, "alice", f"Remark {index}") for index in range(5)]
    pull.reviews = [review(600 + index, "bob", f"Review {index}", commit="head") for index in range(5)]
    pull.check_runs = [check_run(index, f"job{index}", commit="head") for index in range(5)]
    pull.statuses = [commit_status(index, f"ci/{index}") for index in range(5)]
    pull.workflow_runs = [workflow_run(index, "head", workflow_id=index) for index in range(5)]

    snapshot = _read(host)[0].snapshot(1)

    assert len(snapshot.comments) == len(snapshot.reviews) == 5
    assert [len(snapshot.checks_of(kind)) for kind in CheckKind] == [5, 5, 5]


def test_check_results_are_tied_to_the_commit_they_ran_against() -> None:
    host = FakePullRequestHost()
    pull = host.add(1, head_sha="head")
    pull.check_runs = [check_run(1, "lint"), check_run(2, "test", commit="head")]
    pull.statuses = [commit_status(3, "ci/legacy")]
    pull.workflow_runs = [workflow_run(4, "head", conclusion="failure")]

    snapshot = _read(host)[0].snapshot(1)

    assert [(check.kind, check.name, check.commit) for check in snapshot.checks] == [
        (CheckKind.CHECK_RUN, "lint", "head"),
        (CheckKind.CHECK_RUN, "test", "head"),
        (CheckKind.STATUS, "ci/legacy", "head"),
        (CheckKind.WORKFLOW_RUN, "", "head"),
    ]
    assert [check.passing("head") for check in snapshot.checks] == [True, True, True, False]


def test_a_check_result_for_another_commit_is_stale_and_never_passes() -> None:
    host = FakePullRequestHost()
    pull = host.add(1, head_sha="head")
    pull.check_runs = [check_run(1, "test", commit="older"), check_run(2, "lint", commit="head")]
    pull.workflow_runs = [workflow_run(3, "older"), workflow_run(4, "head", workflow_id=2)]

    snapshot = _read(host)[0].snapshot(1)

    assert [check.id for check in snapshot.stale_checks] == [1, 3]
    assert [check.id for check in snapshot.current_checks] == [2, 4]
    assert [check.passing("head") for check in snapshot.checks] == [False, True, False, True]


def test_a_new_head_commit_makes_every_earlier_result_stale() -> None:
    host = FakePullRequestHost()
    pull = host.add(1, head_sha="first")
    pull.check_runs = [check_run(1, "test", commit="first")]
    pulls = _read(host)[0]
    assert pulls.snapshot(1).stale_checks == ()

    pull.head_sha = "second"
    pull.check_runs = [check_run(1, "test", commit="first")]

    snapshot = pulls.snapshot(1)
    assert [check.id for check in snapshot.stale_checks] == [1] and not snapshot.checks[0].passing("second")


def test_a_closed_pull_request_is_read_without_its_discussion_or_checks() -> None:
    host = FakePullRequestHost()
    host.add(1).close(merged=True)
    pulls, gh = _read(host)

    snapshot = pulls.snapshot(1)

    assert not snapshot.pull_request.is_open
    assert (snapshot.threads, snapshot.reviews, snapshot.comments, snapshot.checks) == ((), (), (), ())
    assert len(gh.calls) == 1


def test_an_open_snapshot_carries_the_review_decision_and_merge_status() -> None:
    host = FakePullRequestHost()
    pull = host.add(1)
    pull.review_decision, pull.merge_state_status = "CHANGES_REQUESTED", "BLOCKED"

    snapshot = _read(host)[0].snapshot(1)

    assert (snapshot.review_decision, snapshot.merge_state_status) == (ReviewDecision.CHANGES_REQUESTED, MergeState.BLOCKED)


def test_a_review_decision_or_merge_status_github_does_not_name_reads_as_unknown() -> None:
    host = FakePullRequestHost()
    pull = host.add(1)
    pull.review_decision, pull.merge_state_status = "SOMETHING_NEW", "SOMETHING_NEW"

    snapshot = _read(host)[0].snapshot(1)

    assert (snapshot.review_decision, snapshot.merge_state_status) == (ReviewDecision.UNKNOWN, MergeState.UNKNOWN)


def test_a_read_that_cannot_complete_raises_instead_of_returning_less() -> None:
    host = FakePullRequestHost()
    host.add(1)
    pulls, gh = _read(host)
    gh.fail_on("api", "graphql")

    with pytest.raises(CommandError):
        pulls.snapshot(1)


def test_a_pull_request_that_does_not_exist_cannot_be_read() -> None:
    with pytest.raises(CommandError, match="404"):
        _read(FakePullRequestHost())[0].pull_request(9)

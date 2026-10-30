from __future__ import annotations

import pytest

from loop.shared.github.issues.client import Issues
from loop.shared.github.issues.errors import IncompleteReadError, IssueError
from loop.shared.github.repo import GitHubRepo
from loop.shared.process import CommandError
from tests.shared.fakes import FakeGhCli, issue

_SPEC_LABELS = ("spec", "repo:target:o/r", "repo:base:main")


def _issues(gh: FakeGhCli) -> Issues:
    return Issues(GitHubRepo("owner", "repo", gh=gh))


def _empty_spec_gh() -> FakeGhCli:
    return FakeGhCli(tickets={1: []}, specs=[issue(1, "Add login page", labels=_SPEC_LABELS)])


def test_each_command_is_a_gh_write_sent_in_the_order_issued() -> None:
    gh = FakeGhCli()
    issues = _issues(gh)

    issues.add_label(1, "hitl")
    issues.comment(1, "hand-off", "hello")
    issues.remove_label(1, "spec")
    issues.close(10, "done")

    assert [call[:3] for call in gh.writes()] == [
        ("issue", "edit", "1"),
        ("api", "--method", "POST"),
        ("issue", "edit", "1"),
        ("issue", "close", "10"),
    ]
    assert gh.writes()[0][-2:] == ("--add-label", "hitl")
    assert gh.writes()[2][-2:] == ("--remove-label", "spec")


def test_close_and_add_label_decide_from_one_read_of_the_issue_without_its_comments() -> None:
    gh = FakeGhCli(specs=[issue(1, "Busy", comments=("a", "b"))], tickets={})
    issues = _issues(gh)

    issues.add_label(1, "hitl")
    issues.close(1, "done")

    assert [call[-1] for call in gh.calls if call[0] == "api"] == ["repos/owner/repo/issues/1"] * 2


def test_a_change_is_on_github_before_the_next_step_reads_it() -> None:
    gh = FakeGhCli()
    issues = _issues(gh)

    issues.add_label(1, "hitl")
    issues.comment(1, "hand-off", "hello")
    spec = next(iter(issues.specs()))

    assert "hitl" in spec.labels
    assert spec.comments[-1].body.startswith("hello")


def test_a_posted_comment_is_addressed_by_its_issue_and_its_own_id() -> None:
    issues = _issues(FakeGhCli())

    comment = issues.comment(1, "hand-off", "hello")

    assert (comment.ref.issue.number, comment.ref.issue.repository.repo) == (1, "repo")
    assert comment.ref.id == issues.issue(1).comments[-1].ref.id
    assert comment.author == "loop" and comment.body.startswith("hello")


def test_a_failing_write_raises_where_it_happens_and_keeps_the_earlier_writes() -> None:
    gh = FakeGhCli()
    gh.fail_on("api", "--method", "POST", "repos/owner/repo/issues/1/comments")
    issues = _issues(gh)

    issues.add_label(1, "hitl")
    with pytest.raises(CommandError, match="scheduled failure"):
        issues.comment(1, "hand-off", "stuck")

    spec = next(iter(issues.specs()))
    assert "hitl" in spec.labels
    assert [comment.body for comment in spec.comments] == ["Comment on #1"]


def test_a_retry_rereads_state_and_skips_the_label_already_applied() -> None:
    gh = FakeGhCli()
    gh.fail_on("api", "--method", "POST", "repos/owner/repo/issues/1/comments")
    issues = _issues(gh)
    issues.add_label(1, "hitl")
    with pytest.raises(CommandError):
        issues.comment(1, "hand-off", "stuck")
    reads_before = len(gh.calls_matching("api", "repos/owner/repo/issues/1"))

    issues.add_label(1, "hitl")
    issues.comment(1, "hand-off", "stuck")

    assert len(gh.calls_matching("api", "repos/owner/repo/issues/1")) > reads_before
    assert [call[-1] for call in gh.writes() if "--add-label" in call] == ["hitl"]
    assert [number for number, _ in gh.posted_comments()] == [1, 1]


def test_a_removed_label_that_is_already_absent_sends_no_write() -> None:
    gh = FakeGhCli()

    _issues(gh).remove_label(1, "hitl")

    assert gh.writes() == []


def test_a_comment_whose_fingerprint_is_already_on_the_issue_is_not_posted_twice() -> None:
    gh = FakeGhCli()
    issues = _issues(gh)

    first = issues.comment(1, "verification", "dev: app verification **pass**")
    again = issues.comment(1, "verification", "dev: app verification **pass**")
    issues.comment(1, "verification", "dev: app verification **fail**")
    issues.comment(1, "review-summary", "dev: app verification **pass**")

    assert again.ref == first.ref
    assert [body.split("\n")[0] for _, body in gh.posted_comments()] == [
        "dev: app verification **pass**",
        "dev: app verification **fail**",
        "dev: app verification **pass**",
    ]


def test_a_comment_is_not_posted_when_its_issue_comments_were_not_fully_read() -> None:
    gh = FakeGhCli()
    gh.fail_on("api", "--paginate", "--slurp", "repos/owner/repo/issues/1/comments?per_page=100")

    with pytest.raises(IncompleteReadError):
        _issues(gh).comment(1, "hand-off", "stuck")

    assert gh.posted_comments() == []


def test_a_closed_issue_is_not_closed_again() -> None:
    gh = FakeGhCli()
    issues = _issues(gh)

    issues.close(10, "done")
    issues.close(10, "done again")

    assert [call[1:3] for call in gh.writes()] == [("close", "10")]


def test_a_crash_between_creating_and_linking_a_ticket_is_completed_on_retry_without_a_second_ticket() -> None:
    gh = _empty_spec_gh()
    issues = _issues(gh)
    spec = next(iter(issues.specs()))
    gh.fail_on("api", "--method", "POST", "repos/owner/repo/issues/1/sub_issues")

    created = issues.create_ticket(spec, "0-add-basket", "Add basket", "Create a basket")
    with pytest.raises(CommandError):
        issues.link_ticket(spec, created)

    found = issues.find_tickets(spec).get("0-add-basket")
    assert found is not None and found.number == created.number
    issues.link_ticket(spec, found)

    assert len(gh.created_issues()) == 1
    assert [ticket.number for ticket in next(iter(issues.specs())).tickets] == [created.number]


def test_a_ticket_already_linked_to_its_spec_is_not_linked_again() -> None:
    gh = _empty_spec_gh()
    issues = _issues(gh)
    spec = next(iter(issues.specs()))
    created = issues.create_ticket(spec, "0-add-basket", "Add basket", "Create a basket")

    issues.link_ticket(spec, created)
    issues.link_ticket(spec, created)

    assert len(gh.calls_matching("api", "--method", "POST", "repos/owner/repo/issues/1/sub_issues")) == 1


def test_linking_sends_the_ticket_rest_id() -> None:
    gh = _empty_spec_gh()
    issues = _issues(gh)
    spec = next(iter(issues.specs()))
    created = issues.create_ticket(spec, "0-add-basket", "Add basket", "Create a basket")

    issues.link_ticket(spec, created)

    [link] = gh.calls_matching("api", "--method", "POST", "repos/owner/repo/issues/1/sub_issues")
    assert link[-2:] == ("-F", f"sub_issue_id={created.identity.id}")


def _two_tickets(issues: Issues):
    spec = next(iter(issues.specs()))
    return issues.create_ticket(spec, "0-first", "First", "one"), issues.create_ticket(spec, "1-second", "Second", "two")


def test_a_blocked_by_dependency_sends_the_blockers_internal_id_not_its_number() -> None:
    gh = _empty_spec_gh()
    issues = _issues(gh)
    first, second = _two_tickets(issues)

    issues.add_blocked_by(second, first)

    [call] = gh.calls_matching("api", "--method", "POST", f"repos/owner/repo/issues/{second.number}/dependencies/blocked_by")
    assert call[-2:] == ("-F", f"issue_id={first.identity.id}")
    assert first.identity.id != first.number
    assert gh.blockers_of(second.number) == [first.number]


def test_a_blocked_by_dependency_already_on_github_is_not_added_again() -> None:
    gh = _empty_spec_gh()
    issues = _issues(gh)
    first, second = _two_tickets(issues)

    issues.add_blocked_by(second, first)
    issues.add_blocked_by(second, first)

    assert len(gh.calls_matching("api", "--method", "POST", f"repos/owner/repo/issues/{second.number}/dependencies/blocked_by")) == 1
    assert gh.blockers_of(second.number) == [first.number]


def test_a_blocked_by_dependency_is_not_added_when_the_existing_ones_cannot_be_read() -> None:
    gh = _empty_spec_gh()
    issues = _issues(gh)
    first, second = _two_tickets(issues)
    gh.deny_on("api", "--paginate", "--slurp", f"repos/owner/repo/issues/{second.number}/dependencies/blocked_by?per_page=100")

    with pytest.raises(CommandError, match="HTTP 403"):
        issues.add_blocked_by(second, first)

    assert gh.writes()[-1][3].endswith("/issues") and gh.blockers_of(second.number) == []


def test_find_tickets_ignores_other_specs_and_closed_issues() -> None:
    gh = _empty_spec_gh()
    issues = _issues(gh)
    spec = next(iter(issues.specs()))
    created = issues.create_ticket(spec, "0-add-basket", "Add basket", "Create a basket")

    assert list(issues.find_tickets(spec)) == ["0-add-basket"]
    other = next(iter(_issues(FakeGhCli(specs=[issue(2, "Other", labels=_SPEC_LABELS)], tickets={})).specs()))
    assert issues.find_tickets(other) == {}
    issues.close(created.number, "done")
    assert issues.find_tickets(spec) == {}


def test_find_tickets_follows_every_page_of_the_routing_label_listing() -> None:
    gh = FakeGhCli(tickets={1: []}, specs=[issue(1, "Add login page", labels=_SPEC_LABELS)], page_size=1)
    issues = _issues(gh)
    spec = next(iter(issues.specs()))
    first = issues.create_ticket(spec, "0-first", "First", "one")
    second = issues.create_ticket(spec, "1-second", "Second", "two")

    found = issues.find_tickets(spec)

    assert {key: ticket.number for key, ticket in found.items()} == {"0-first": first.number, "1-second": second.number}


def test_a_created_ticket_carries_the_routing_label_a_hidden_marker_and_a_stable_identity() -> None:
    gh = _empty_spec_gh()
    issues = _issues(gh)
    spec = next(iter(issues.specs()))

    created = issues.create_ticket(spec, "0-add-basket", "Add basket", "Create a basket")

    [call] = gh.calls_matching("api", "--method", "POST", "repos/owner/repo/issues")
    assert "labels[]=repo:target:o/r" in call
    assert created.labels == ("repo:target:o/r",)
    assert created.body == "Create a basket\n\n<!-- loop:ticket spec=1 key=0-add-basket -->"
    assert (created.identity.id, created.ref.number) == (1000 + created.number, created.number)


def test_a_ticket_cannot_be_created_for_a_spec_without_a_valid_target() -> None:
    gh = FakeGhCli(tickets={1: []}, specs=[issue(1, "Add login page", labels=("spec",))])
    issues = _issues(gh)

    with pytest.raises(IssueError, match="target repository"):
        issues.create_ticket(next(iter(issues.specs())), "0-x", "X", "x")

    assert gh.writes() == []


def test_non_zero_gh_exit_raises_a_command_error_with_the_command_and_output() -> None:
    gh = FakeGhCli()
    gh.fail_on("api", output="not found")

    with pytest.raises(CommandError, match="not found"):
        _issues(gh).comment(1, "hand-off", "x")

"""Scenarios of the implement_spec entry point over fake services: what the tracker, the branch and the exit code show after each run."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.shared.fakes import issue
from workflows.tests.software_factory.stages.support import BRANCH, LABELS

from .harness import TWO_TICKETS, ImplementSpecHarness


def test_every_actionable_ticket_is_delivered_then_published_reviewed_and_made_ready(tmp_path: Path) -> None:
    """Given two ready Tickets, when the Spec runs, then each is closed with its commit, the branch is pushed, one draft pull request carries its link on the Spec and the run exits 0."""
    harness = ImplementSpecHarness(tmp_path, tickets=TWO_TICKETS)

    code = harness.run()

    assert code == 0
    assert harness.closed() == [10, 11]
    assert [subject.split(": ")[0] for subject in harness.branch_subjects()] == ["ccode(Checkout|10)", "ccode(Checkout|11)"]
    assert harness.rig.remote_head(BRANCH) == harness.branch_head()
    assert harness.pull_request_creations() == 1
    assert any("draft pull request: https://github.com/owner/repo/pull/99" in body for body in harness.rig.comments(1))
    assert harness.agents.developer_runs() == 2 and len(harness.agents.prompts["chore"]) == 1 and len(harness.agents.prompts["reviewer"]) == 1
    assert harness.rig.gh.calls_matching("pr", "ready")
    assert any("ready for review" in body for body in harness.rig.comments(1))
    assert harness.labels(1) == set(LABELS)


def test_a_chore_commit_is_pushed_and_is_the_head_the_review_and_finalization_judge(tmp_path: Path) -> None:
    """Given a chore agent that edits a file, when the Spec runs, then the chore commit is on the remote branch, the reviewer saw that head and the pull request is made ready."""
    harness = ImplementSpecHarness(tmp_path, tickets=TWO_TICKETS)
    harness.agents.chore_edit = "tidied\n"

    code = harness.run()

    assert code == 0
    assert harness.branch_subjects()[-1].startswith("chore(")
    assert harness.rig.remote_head(BRANCH) == harness.branch_head()
    assert harness.branch_head() in harness.agents.prompts["reviewer"][0]
    assert harness.rig.gh.calls_matching("pr", "ready")


def test_a_developer_that_fails_twice_escalates_the_ticket_and_the_spec_and_the_run_exits_1(tmp_path: Path) -> None:
    """Given a developer that never commits, when the Spec runs with the default cap, then Ticket and Spec carry hitl with the reason on both and exactly two attempts failed."""
    harness = ImplementSpecHarness(tmp_path)
    harness.agents.actions[10] = ["fail"]

    code = harness.run()

    assert code == 1
    assert harness.labels(10) == {"hitl"} and "hitl" in harness.labels(1)
    assert "tests for #10 failed" in harness.rig.comments(10)[0] and "tests for #10 failed" in harness.rig.comments(1)[0]
    assert harness.outcomes("implement-ticket") == ["fail", "fail"]
    assert harness.agents.developer_runs() == 2 and harness.closed() == []
    assert harness.pull_request_creations() == 0 and not harness.rig.gh.calls_matching("pr", "ready")


def test_a_ticket_that_passes_on_the_second_attempt_is_delivered_without_escalation(tmp_path: Path) -> None:
    """Given a developer that fails once, when the Spec runs, then the second attempt delivers the Ticket and nothing is labelled."""
    harness = ImplementSpecHarness(tmp_path)
    harness.agents.actions[10] = ["fail", "commit"]

    code = harness.run()

    assert code == 0 and harness.outcomes("implement-ticket") == ["fail", "pass"]
    assert harness.closed() == [10] and "hitl" not in harness.labels(10) | harness.labels(1)


def test_a_ticket_that_commits_nothing_is_rejected_like_a_failure(tmp_path: Path) -> None:
    """Given a developer that reports a commit it did not make, when the Spec runs, then both attempts fail and the Ticket is escalated."""
    harness = ImplementSpecHarness(tmp_path)
    harness.agents.actions[10] = ["nothing"]

    code = harness.run()

    assert code == 1 and harness.outcomes("implement-ticket") == ["fail", "fail"]
    assert "hitl" in harness.labels(10)


def test_only_tickets_held_by_dependencies_end_the_run_idle_without_labelling_the_spec(tmp_path: Path) -> None:
    """Given every open Ticket held by a dependency or a person, when the Spec runs, then no agent runs, nothing is labelled and the run exits 0."""
    tickets = [issue(10, "Waits for a person", labels=("hitl",)), issue(11, "Waits for ten")]
    harness = ImplementSpecHarness(tmp_path, tickets=tickets, blocked_by={11: [10]})

    code = harness.run()

    assert code == 0
    assert harness.agents.developer_runs() == 0 and not harness.opened_worktree()
    assert harness.labels(1) == set(LABELS) and harness.rig.gh.writes() == []


@pytest.mark.parametrize(
    "labels",
    [
        ("spec", "repo:base:main"),
        ("spec", "repo:target:not-a-slug", "repo:base:main"),
        ("spec", "repo:target:acme/widgets", "repo:base:main"),
        ("spec", "repo:target:owner/repo", "repo:target:other/one", "repo:base:main"),
    ],
)
def test_an_unroutable_spec_is_labelled_hitl_with_a_comment_and_no_worktree_is_opened(tmp_path: Path, labels: tuple[str, ...]) -> None:
    """Given a Spec whose target is missing, malformed, unconfigured or duplicated, when the Spec runs, then it carries hitl with a comment and nothing is fetched or opened."""
    harness = ImplementSpecHarness(tmp_path, labels=labels)

    code = harness.run()

    assert code == 1
    assert "hitl" in harness.labels(1) and harness.rig.comments(1)
    assert not harness.opened_worktree() and harness.agents.developer_runs() == 0
    assert harness.rig.runner.git("fetch") == []


def test_a_missing_base_branch_hands_the_spec_to_a_human_before_any_worktree_opens(tmp_path: Path) -> None:
    """Given a base branch that is not on origin, when the Spec runs, then it carries hitl with a comment naming the branch and no worktree is opened."""
    harness = ImplementSpecHarness(tmp_path, labels=("spec", "repo:target:owner/repo", "repo:base:release/9"))

    code = harness.run()

    assert code == 1 and "hitl" in harness.labels(1)
    assert "release/9" in harness.rig.comments(1)[0]
    assert not harness.opened_worktree() and harness.agents.developer_runs() == 0


def test_a_spec_labelled_hitl_is_not_taken_up(tmp_path: Path) -> None:
    """Given a Spec that awaits a person, when the entry point runs, then no agent runs and nothing is written."""
    harness = ImplementSpecHarness(tmp_path, labels=(*LABELS, "hitl"))

    code = harness.run()

    assert code == 0 and harness.agents.developer_runs() == 0 and harness.rig.gh.writes() == []
    assert not harness.opened_worktree()


def test_a_spec_that_was_not_read_completely_is_left_alone_and_fails_the_run(tmp_path: Path) -> None:
    """Given a Spec whose sub-issues cannot be read, when the entry point runs, then nothing is written, no agent runs and it exits 1."""
    harness = ImplementSpecHarness(tmp_path)
    harness.rig.gh.fail_on("api", "--paginate", "--slurp", "repos/owner/repo/issues/1/sub_issues?per_page=100")

    code = harness.run()

    assert code == 1 and harness.rig.gh.writes() == [] and harness.agents.developer_runs() == 0


def test_asking_for_a_spec_that_is_not_open_fails(tmp_path: Path) -> None:
    """Given no open Spec 9, when the entry point is asked for it, then it exits 1 without running anything."""
    harness = ImplementSpecHarness(tmp_path)

    code = harness.run("--spec", "9")

    assert code == 1 and harness.agents.developer_runs() == 0 and harness.rig.gh.writes() == []

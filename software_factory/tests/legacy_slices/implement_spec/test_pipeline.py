"""The eShop-style phases through the public implement_spec entry point and process fakes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from software_factory.tests.legacy_slices.implement_spec.doubles import ImplementSpecHarness, clean_review, crash, envelope
from software_factory.tests.shared.fakes import issue


def _deliver(harness: ImplementSpecHarness, **kwargs) -> int:
    return harness.run(lambda prompt: harness.commit_and_report(10), all_phases=True, **kwargs)


def test_default_pipeline_publishes_then_reviews_after_worktree_closure(tmp_path: Path, capsys) -> None:
    """Given a deliverable Ticket, when implement_spec runs, then the full default pipeline checks readiness."""
    harness = ImplementSpecHarness(tmp_path)
    closed = []
    def review(prompt: str) -> str:
        closed.append(harness.git_service.closed)
        return clean_review(harness.git.commits[-1].sha)
    harness.runner.review_handler = review
    assert _deliver(harness) == 0
    assert closed == [1]
    assert [profile.name for profile in harness.runner.profiles] == ["developer", "chore", "reviewer"]
    assert len(harness.calls("pr", "ready")) == 1
    assert not harness.calls("pr", "merge")
    assert all(call[2] != "1" for call in harness.calls("issue", "close"))
    log = capsys.readouterr().err
    assert "Plan tickets · spec #1 skipped" in log
    assert log.index("Publish draft PR · spec #1 completed") < log.index("Refine changes · spec #1 started")
    assert log.index("Deliver tickets · spec #1 completed") < log.index("Review pull request · spec #1 started")
    assert "ready=True" in log and "worktree opened" not in log and "dry-run:" not in log


def test_default_planner_creates_subissues_and_delivers_in_dependency_order(tmp_path: Path) -> None:
    """Given a Spec with no sub-issues, when planning succeeds, then created Tickets are delivered in order."""
    harness = ImplementSpecHarness(tmp_path, tickets={1: []})
    harness.runner.plan_handler = lambda prompt: envelope("", result={"tickets": [
        {"title": "Add basket", "body": "Create a basket"},
        {"title": "Add coffee", "body": "Add a product", "blocked_by": [0]},
    ]})
    numbers = iter((101, 102))
    assert harness.run(lambda prompt: harness.commit_and_report(next(numbers)), all_phases=True) == 0
    creates = harness.gh.created_issues()
    assert len(creates) == 2
    assert not any("Blocked by" in body for _, body in creates)
    assert harness.gh.blockers_of(101) == [] and harness.gh.blockers_of(102) == [101]
    [edge] = harness.calls("api", "--method", "POST", "repos/owner/repo/issues/102/dependencies/blocked_by")
    assert edge[-2:] == ("-F", "issue_id=1101")
    assert [call[2] for call in harness.calls("issue", "close")] == ["101", "102"]
    assert len(harness.git.pushes) == 2
    assert [profile.name for profile in harness.runner.profiles] == ["planner", "developer", "developer", "chore", "reviewer"]


def test_a_retry_after_a_crash_between_creating_and_linking_a_ticket_links_it_instead_of_creating_another(
    tmp_path: Path,
) -> None:
    """Given a Ticket created but not linked, when the human releases the Spec and implement_spec reruns, then that Ticket is linked."""
    harness = ImplementSpecHarness(tmp_path, tickets={1: []})
    harness.runner.plan_handler = lambda prompt: envelope("", result={"tickets": [{"title": "Add basket", "body": "Create a basket"}]})
    harness.gh.fail_on("api", "--method", "POST", "repos/owner/repo/issues/1/sub_issues")
    assert harness.run(all_phases=True) == 1
    assert len(harness.gh.created_issues()) == 1
    assert harness.calls("issue", "edit")[0][-1] == "hitl"
    harness.tracker.remove_label(1, "hitl")
    harness.gh.calls.clear()

    assert harness.run(lambda prompt: harness.commit_and_report(101), all_phases=True) == 0

    assert not harness.gh.created_issues()
    assert len(harness.runner.plan_prompts) == 1
    assert len(harness.calls("api", "--method", "POST", "repos/owner/repo/issues/1/sub_issues")) == 1
    assert [call[2] for call in harness.calls("issue", "close")] == ["101"]


def test_a_retry_after_the_second_ticket_failed_to_link_finishes_the_persisted_plan_instead_of_delivering_a_partial_set(
    tmp_path: Path,
) -> None:
    """Given the Spec already has its first Ticket linked, when linking the second failed, then the retry completes the plan."""
    harness = ImplementSpecHarness(tmp_path, tickets={1: []})
    harness.runner.plan_handler = lambda prompt: envelope("", result={"tickets": [
        {"title": "Add basket", "body": "Create a basket --> <b>safe</b>"},
        {"title": "Add coffee", "body": "Add a product", "blocked_by": [0]},
    ]})
    harness.gh.fail_on("api", "--method", "POST", "repos/owner/repo/issues/1/sub_issues", after=1)
    assert harness.run(all_phases=True) == 1
    assert [title for title, _ in harness.gh.created_issues()] == ["Add basket", "Add coffee"]
    assert harness.git.pushes == [] and not harness.calls("issue", "close")
    harness.tracker.remove_label(1, "hitl")
    harness.gh.calls.clear()
    numbers = iter((101, 102))

    assert harness.run(lambda prompt: harness.commit_and_report(next(numbers)), all_phases=True) == 0

    assert not harness.gh.created_issues()
    assert len(harness.runner.plan_prompts) == 1
    assert [call[2] for call in harness.calls("issue", "close")] == ["101", "102"]


def test_a_planned_spec_is_not_replanned_and_its_edges_are_not_added_again(tmp_path: Path) -> None:
    """Given a planned and delivered Spec, when implement_spec reruns, then it is not replanned and no edge or Ticket is written."""
    harness = ImplementSpecHarness(tmp_path, tickets={1: []})
    harness.runner.plan_handler = lambda prompt: envelope("", result={"tickets": [
        {"title": "Add basket", "body": "Create a basket"},
        {"title": "Add coffee", "body": "Add a product", "blocked_by": [0]},
    ]})
    numbers = iter((101, 102))
    assert harness.run(lambda prompt: harness.commit_and_report(next(numbers)), all_phases=True) == 0
    harness.gh.calls.clear()

    assert harness.run(all_phases=True) == 0

    assert not harness.gh.created_issues() and not harness.calls("api", "--method", "POST")


@pytest.mark.parametrize("reply", ["no JSON", envelope("", result={"tickets": []}),
                                    envelope("", "failed", {"reason": "needs a decision"})])
def test_planning_failure_hands_off_without_opening_an_implementation_worktree(tmp_path: Path, reply: str) -> None:
    """Given an unusable plan, when implement_spec runs, then it reports failure and leaves delivery to a human."""
    harness = ImplementSpecHarness(tmp_path, tickets={1: []})
    harness.runner.plan_handler = lambda prompt: reply
    assert harness.run(all_phases=True) == 1
    assert not harness.gh.created_issues()
    assert not harness.git_service.opened
    assert harness.calls("issue", "edit")[0][-1] == "hitl"


def test_closed_or_held_subissues_do_not_trigger_another_plan(tmp_path: Path) -> None:
    """Given existing closed Tickets, when a Spec has no work, then it is not replanned."""
    harness = ImplementSpecHarness(tmp_path, tickets={1: [issue(10, "Done", state="CLOSED")]})
    assert harness.run(all_phases=True) == 0
    assert not harness.runner.plan_prompts
    assert not harness.gh.created_issues()


def test_cleanup_changes_are_committed_and_reviewed_at_their_final_head(tmp_path: Path) -> None:
    """Given cleanup edits, when delivery finishes, then review covers the cleanup commit."""
    harness = ImplementSpecHarness(tmp_path)
    def chore(prompt: str) -> str:
        assert (harness.worktree / "bin/review_diff/_manifest.tsv").exists()
        harness.git.edit("src/Basket.cs")
        return envelope("", result={"commit_message": "Simplify basket", "summary": "Removed duplication"})
    harness.runner.chore_handler = chore
    assert _deliver(harness) == 0
    assert harness.git.commits[-1].subject == "chore(Checkout): Simplify basket"
    assert harness.git.commits[-1].sha in harness.runner.review_prompts[0]
    assert not (harness.worktree / "bin/review_diff").exists()


def test_cleanup_failure_is_discarded_and_does_not_block_a_clean_review(tmp_path: Path) -> None:
    """Given a best-effort cleanup failure, when delivery continues, then the delivered commit remains reviewable."""
    harness = ImplementSpecHarness(tmp_path)
    harness.runner.chore_handler = crash()
    assert _deliver(harness) == 0
    assert len(harness.git.commits) == 2
    assert harness.git.restores
    assert "Chore: discarded" in harness.comments()[-1]


@pytest.mark.parametrize("mode", ["bug", "wrong-head", "missing-axis", "crash", "checkout-change"])
def test_review_and_readiness_blockers_keep_the_draft(tmp_path: Path, mode: str) -> None:
    """Given a review blocker, when readiness is checked, then implement_spec fails without publishing readiness."""
    harness = ImplementSpecHarness(tmp_path, tickets={1: [issue(10, "Add basket")]})
    def review(prompt: str) -> str:
        result = json.loads(clean_review(harness.git.commits[-1].sha))
        if mode == "bug":
            result["result"]["findings"] = [{"kind": "bug", "summary": "Basket loses products"}]
        elif mode == "wrong-head":
            result["result"]["head"] = "old"
        elif mode == "missing-axis":
            result["result"]["axes"].pop()
        elif mode == "checkout-change":
            harness.git.edit("src/Basket.cs")
        return json.dumps(result)
    harness.runner.review_handler = crash() if mode == "crash" else review
    assert _deliver(harness) == 1
    assert not harness.calls("pr", "ready")
    assert "left as a draft" in harness.comments()[-1]


def test_a_spec_with_a_held_ticket_is_published_but_not_announced_or_reviewed(tmp_path: Path) -> None:
    """Given a Ticket still held for a human, when the ready Tickets land, then implement_spec stops at the draft PR instead of claiming completion."""
    harness = ImplementSpecHarness(tmp_path, tickets={1: [issue(10, "Add basket"), issue(11, "Human decision", labels=("hitl",))]})
    harness.runner.review_handler = crash()

    assert _deliver(harness) == 0

    assert [profile.name for profile in harness.runner.profiles] == ["developer"]
    assert not any("all Tickets delivered" in comment for comment in harness.comments())
    assert not harness.calls("pr", "ready")


def test_spec_selection_and_visualization_report_the_default_pipeline(tmp_path: Path, capsys) -> None:
    """Given a selected Spec, when reporting is visualized, then the default pipeline supplies its phase table."""
    pytest.importorskip("rich")
    harness = ImplementSpecHarness(tmp_path)
    assert _deliver(harness, argv=["--log-dir", str(harness.log_dir), "--spec", "1", "--visualize"]) == 0
    assert "Check readiness" in capsys.readouterr().err
    assert _deliver(harness, argv=["--spec", "999"]) == 1

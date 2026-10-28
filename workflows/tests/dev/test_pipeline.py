"""The eShop-style phases through the public dev entry point and process fakes."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from workflows.dev.tracker.tickets import TicketsTracker
from workflows.tests.dev.doubles import DevHarness, clean_review, crash, envelope
from workflows.tests.shared.fakes import issue


def _deliver(harness: DevHarness, **kwargs) -> int:
    return harness.run(lambda prompt: harness.commit_and_report(10), all_phases=True, **kwargs)


def _skill(harness: DevHarness) -> None:
    def on_open() -> None:
        path = harness.worktree / ".github/skills/verify-basket/SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nname: verify-basket\ndescription: Drive and prove the basket in the app.\n---\n")
    harness.git_service.on_open = on_open


def _verifier(harness: DevHarness, verdict: str = "pass", *, evidence: bool = True, edit: str = "", commit: bool = False):
    def verify(prompt: str) -> str:
        if evidence:
            directory = Path(re.search(r"`([^`]*evidence[^`]*)`", prompt)[1])
            (directory / "basket.json").write_text('{"products": ["coffee"]}')
        if edit:
            harness.git.edit(edit)
        if commit:
            harness.git.add_commit("rejected verifier commit")
        return envelope("", result={"verdict": verdict, "summary": "Checked the basket", "commit_message": "Fix basket"})
    harness.runner.verify_handler = verify


def test_default_pipeline_publishes_then_reviews_after_worktree_closure(tmp_path: Path, capsys) -> None:
    """Given a deliverable Ticket, when dev runs, then the full default pipeline checks readiness."""
    harness = DevHarness(tmp_path)
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
    assert "Verify application · spec #1 skipped" in log
    assert log.index("Publish draft PR · spec #1 completed") < log.index("Refine changes · spec #1 started")
    assert log.index("Deliver tickets · spec #1 completed") < log.index("Review pull request · spec #1 started")
    assert "ready=True" in log and "worktree opened" not in log and "dry-run:" not in log


def test_default_planner_creates_subissues_and_delivers_in_dependency_order(tmp_path: Path) -> None:
    """Given a Spec with no sub-issues, when planning succeeds, then created Tickets are delivered in order."""
    harness = DevHarness(tmp_path, tickets={1: []})
    harness.runner.plan_handler = lambda prompt: envelope("", result={"tickets": [
        {"title": "Add basket", "body": "Create a basket"},
        {"title": "Add coffee", "body": "Add a product", "blocked_by": [0]},
    ]})
    numbers = iter((101, 102))
    assert harness.run(lambda prompt: harness.commit_and_report(next(numbers)), all_phases=True) == 0
    creates = harness.calls("issue", "create")
    assert len(creates) == 2
    assert "Blocked by #101" in creates[1][-1]
    assert [call[2] for call in harness.calls("issue", "close")] == ["101", "102"]
    assert len(harness.git.pushes) == 2
    assert [profile.name for profile in harness.runner.profiles] == ["planner", "developer", "developer", "chore", "reviewer"]


def test_a_retry_after_a_crash_between_creating_and_linking_a_ticket_links_it_instead_of_creating_another(
    tmp_path: Path,
) -> None:
    """Given a Ticket created but not linked, when the human releases the Spec and dev reruns, then that Ticket is linked."""
    harness = DevHarness(tmp_path, tickets={1: []})
    harness.runner.plan_handler = lambda prompt: envelope("", result={"tickets": [{"title": "Add basket", "body": "Create a basket"}]})
    harness.gh.fail_on("api", "--method", "POST")
    assert harness.run(all_phases=True) == 1
    assert len(harness.calls("issue", "create")) == 1
    assert harness.calls("issue", "edit")[0][-1] == "hitl"
    TicketsTracker("owner", "repo", gh=harness.gh).remove_label(1, "hitl")
    harness.gh.calls.clear()

    assert harness.run(lambda prompt: harness.commit_and_report(101), all_phases=True) == 0

    assert not harness.calls("issue", "create")
    assert len(harness.calls("api", "--method", "POST")) == 1
    assert [call[2] for call in harness.calls("issue", "close")] == ["101"]


@pytest.mark.parametrize("reply", ["no JSON", envelope("", result={"tickets": []}),
                                    envelope("", "failed", {"reason": "needs a decision"})])
def test_planning_failure_hands_off_without_opening_an_implementation_worktree(tmp_path: Path, reply: str) -> None:
    """Given an unusable plan, when dev runs, then it reports failure and leaves delivery to a human."""
    harness = DevHarness(tmp_path, tickets={1: []})
    harness.runner.plan_handler = lambda prompt: reply
    assert harness.run(all_phases=True) == 1
    assert not harness.calls("issue", "create")
    assert not harness.git_service.opened
    assert harness.calls("issue", "edit")[0][-1] == "hitl"


def test_closed_or_held_subissues_do_not_trigger_another_plan(tmp_path: Path) -> None:
    """Given existing closed Tickets, when a Spec has no work, then it is not replanned."""
    harness = DevHarness(tmp_path, tickets={1: [issue(10, "Done", state="CLOSED")]})
    assert harness.run(all_phases=True) == 0
    assert not harness.runner.plan_prompts
    assert not harness.calls("issue", "create")


def test_passing_app_verification_saves_evidence_and_commits_only_the_fixes(tmp_path: Path) -> None:
    """Given a verification skill, when a verified fix is proposed, then dev commits and pushes it."""
    harness = DevHarness(tmp_path)
    _skill(harness)
    _verifier(harness, edit="src/Basket.cs")
    assert _deliver(harness) == 0
    assert harness.git.commits[-1].subject == "fix(Checkout): Fix basket"
    assert len(harness.git.pushes) == 2
    summary = harness.calls("issue", "comment")[-1][-1]
    assert "App verification: pass" in summary
    assert list((harness.log_dir / "evidence").rglob("basket.json"))


@pytest.mark.parametrize("verdict,evidence,edit,commit,settled", [
    ("fail", True, "", False, "fail"),
    ("pass", False, "", False, "inconclusive"),
    ("pass", True, "proof/features/basket.py", False, "partial"),
    ("pass", True, "", True, "inconclusive"),
])
def test_unproven_or_rejected_verification_never_marks_the_pr_ready(
    tmp_path: Path, capsys, verdict: str, evidence: bool, edit: str, commit: bool, settled: str,
) -> None:
    """Given a rejected verification result, when finalizing, then the phase and PR stay failed/draft."""
    harness = DevHarness(tmp_path)
    _skill(harness)
    _verifier(harness, verdict, evidence=evidence, edit=edit, commit=commit)
    assert _deliver(harness) == 1
    assert not harness.calls("pr", "ready")
    assert "rejected verifier commit" not in [entry.subject for entry in harness.git.commits]
    assert f"App verification: {settled}" in harness.calls("issue", "comment")[-1][-1]
    log = capsys.readouterr().err
    assert f"Verify application · spec #1 failed" in log and f"verdict={settled}" in log
    assert "Check readiness · spec #1 failed" in log


def test_cleanup_changes_are_committed_and_reviewed_at_their_final_head(tmp_path: Path) -> None:
    """Given cleanup edits, when delivery finishes, then review covers the cleanup commit."""
    harness = DevHarness(tmp_path)
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
    harness = DevHarness(tmp_path)
    harness.runner.chore_handler = crash()
    assert _deliver(harness) == 0
    assert len(harness.git.commits) == 2
    assert harness.git.restores
    assert "Chore: discarded" in harness.calls("issue", "comment")[-1][-1]


@pytest.mark.parametrize("mode", ["bug", "wrong-head", "missing-axis", "crash", "checkout-change", "held-ticket"])
def test_review_and_readiness_blockers_keep_the_draft(tmp_path: Path, mode: str) -> None:
    """Given a review blocker, when readiness is checked, then dev fails without publishing readiness."""
    tickets = {1: [issue(10, "Add basket")]}
    if mode == "held-ticket":
        tickets[1].append(issue(11, "Human decision", labels=("hitl",)))
    harness = DevHarness(tmp_path, tickets=tickets)
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
    assert "left as a draft" in harness.calls("issue", "comment")[-1][-1]


def test_spec_selection_and_visualization_report_the_default_pipeline(tmp_path: Path, capsys) -> None:
    """Given a selected Spec, when reporting is visualized, then the default pipeline supplies its phase table."""
    pytest.importorskip("rich")
    harness = DevHarness(tmp_path)
    assert _deliver(harness, argv=["--log-dir", str(harness.log_dir), "--spec", "1", "--visualize"]) == 0
    assert "Check readiness" in capsys.readouterr().err
    assert _deliver(harness, argv=["--spec", "999"]) == 1

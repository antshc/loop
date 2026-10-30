"""The finalize Stage: the readiness decision, the ready mark and the summary Comment, run as a one-Stage Workflow."""

from __future__ import annotations

from pathlib import Path
from subprocess import CalledProcessError

import pytest

from software_factory.events.chore import ChoreDiscarded, ChoreRefined
from software_factory.events.pull_requests import DraftPullRequestPublished, Finalized
from software_factory.events.review import PullRequestReviewed, ReviewAxis, ReviewFailed, ReviewFinding
from software_factory.events.tickets import Delivery
from software_factory.runtime import Event
from software_factory.stages import finalize
from tests.runtime.harness import command
from tests.shared.fakes import issue
from tests.stages.support import BRANCH, HITL, PULL_REQUEST, StageRig, seeded

AXES = ("bugs", "quality", "smells", "initiative")
DELIVERED = [issue(10, "Add login form", state="CLOSED")]


def clean_review(head: str, *, findings: tuple[ReviewFinding, ...] = (), outcome: str = "completed") -> PullRequestReviewed:
    """A review that covered `head` with every axis `outcome`."""
    return PullRequestReviewed(tuple(ReviewAxis(axis, outcome) for axis in AXES), findings, ("comment-1",), head)


def finalizing(
    rig: StageRig, head: str, review: Event | None, *, chore: Event | None = None, label: str | None = HITL, delivery_head: str | None = None,
):
    """The routing Stage, the delivery, the published pull request, the chore and review evidence (None: skipped), then finalize."""
    delivery = Delivery(delivery_head or head, ("abc1234 ccode(Checkout|10): implement 0",))
    published = DraftPullRequestPublished(PULL_REQUEST, BRANCH, pushed=True)
    skipped = (ChoreDiscarded("unused"), PullRequestReviewed((), (), (), ""))
    stages = [seeded(delivery), seeded(published)]
    for event, absent in ((chore, skipped[0]), (review, skipped[1])):
        stages.append(seeded(event) if event is not None else seeded(absent).when(lambda _: False))
    return rig.workflow(*stages, finalize.finalize(label=label))


def summaries(rig: StageRig) -> list[str]:
    """The summary Comments on the Spec."""
    return [body for body in rig.comments(1) if body.startswith("implement_spec: pull request")]


def is_draft(rig: StageRig) -> bool:
    """Whether the feature branch's pull request is still a draft."""
    return rig.github.pull_requests("owner/repo").find_open(BRANCH).draft


def delivered_rig(tmp_path: Path, tickets: list[dict] | None = None) -> tuple[StageRig, str]:
    """A rig whose Tickets are done and whose feature branch is published under a draft pull request."""
    rig = StageRig(tmp_path, tickets=DELIVERED if tickets is None else tickets)
    return rig, rig.deliver()


def test_every_readiness_check_passing_marks_the_pull_request_ready_and_posts_the_summary_once(tmp_path: Path) -> None:
    """Given a delivered Spec with a clean review of the final head, when finalize runs, then the pull request is ready and one summary says so."""
    rig, head = delivered_rig(tmp_path)
    chore = ChoreRefined("Renamed the helper.", ("old bug",), "tidy", committed=True)

    result = rig.run(finalizing(rig, head, clean_review(head), chore=chore))

    assert result.status == "completed"
    assert rig.history(Finalized) == (Finalized(True, PULL_REQUEST, ()),)
    assert rig.gh.calls_matching("pr", "ready") == [("pr", "ready", PULL_REQUEST, "--repo", "owner/repo")]
    assert not is_draft(rig)
    (summary,) = summaries(rig)
    assert "**ready for review**" in summary and "Kept as a draft" not in summary
    assert "- abc1234 ccode(Checkout|10): implement 0" in summary
    assert "Chore: committed (Renamed the helper.)" in summary and "- out of scope: old bug" in summary
    assert "Review: bugs completed, quality completed, smells completed, initiative completed" in summary
    assert HITL not in rig.labels(1)


def test_a_rerun_after_a_complete_finalization_marks_nothing_ready_and_posts_nothing_again(tmp_path: Path) -> None:
    """Given a finalized Spec, when finalize runs again, then there is no second ready mark and no second summary."""
    rig, head = delivered_rig(tmp_path)
    workflow = finalizing(rig, head, clean_review(head))
    rig.run(workflow)
    writes = len(rig.gh.writes())

    result = rig.run(workflow)

    assert result.status == "completed"
    assert len(rig.gh.writes()) == writes
    assert len(rig.gh.calls_matching("pr", "ready")) == 1 and len(summaries(rig)) == 1


def test_a_rerun_after_a_crash_between_the_ready_mark_and_the_summary_posts_the_summary_without_marking_again(tmp_path: Path) -> None:
    """Given a run that marked the pull request ready and crashed before the summary, when finalize runs again, then only the summary is posted."""
    rig, head = delivered_rig(tmp_path)
    workflow = finalizing(rig, head, clean_review(head))
    rig.gh.fail_on("api", "--method", "POST", "repos/owner/repo/issues/1/comments")

    with pytest.raises(CalledProcessError):
        rig.run(workflow)
    assert not is_draft(rig) and summaries(rig) == []
    result = rig.run(workflow)

    assert result.status == "completed"
    assert len(rig.gh.calls_matching("pr", "ready")) == 1 and len(summaries(rig)) == 1
    assert rig.history(Finalized) == (Finalized(True, PULL_REQUEST, ()),)


@pytest.mark.parametrize(
    "case",
    ["blocking-bug", "blocking-initiative", "incomplete-axis", "missing-axis", "stale-review", "review-failed", "no-review", "moved-head", "held-ticket", "open-ticket"],
)
def test_a_readiness_blocker_keeps_the_pull_request_a_draft_labels_the_spec_and_lists_the_blocker(tmp_path: Path, case: str) -> None:
    """Given one readiness blocker, when finalize runs, then the pull request stays a draft, the Spec is labelled and the summary lists why."""
    tickets = {
        "held-ticket": [*DELIVERED, issue(11, "Needs a person", labels=(HITL,))],
        "open-ticket": [*DELIVERED, issue(11, "Still to do")],
    }.get(case)
    rig, head = delivered_rig(tmp_path, tickets)
    bug = ReviewFinding("bug", "null dereference", "bugs")
    initiative = ReviewFinding("initiative", "misses the goal", "initiative")
    review: PullRequestReviewed | ReviewFailed | None = {
        "blocking-bug": clean_review(head, findings=(bug,)),
        "blocking-initiative": clean_review(head, findings=(initiative,)),
        "incomplete-axis": clean_review(head, outcome="skipped"),
        "missing-axis": PullRequestReviewed((ReviewAxis("bugs", "completed"),), (), (), head),
        "stale-review": clean_review("0" * 40),
        "review-failed": ReviewFailed("could not load the diff"),
        "no-review": None,
    }.get(case, clean_review(head))
    if case == "moved-head":
        rig.repo.write("late.txt", "pushed after the review\n")
        rig.repo.commit("late push")
        command(("push", "-q", "origin", BRANCH), rig.repo.path)
    expected = {
        "blocking-bug": "1 unresolved bug/initiative finding(s)",
        "blocking-initiative": "1 unresolved bug/initiative finding(s)",
        "incomplete-axis": "a review axis did not complete",
        "missing-axis": "a review axis did not complete",
        "stale-review": f"the review covered {'0' * 40}, not {head}",
        "review-failed": "the review failed: could not load the diff",
        "no-review": "the review did not run",
        "moved-head": "the pull request head moved to",
        "held-ticket": "Tickets held (blocked, unread dependencies, or waiting for a human): #11",
        "open-ticket": "open Tickets: #11",
    }[case]

    result = rig.run(finalizing(rig, head, review))

    assert result.status == "completed"
    (finalized,) = rig.history(Finalized)
    assert finalized.ready is False and any(expected in blocker for blocker in finalized.blockers)
    assert rig.gh.calls_matching("pr", "ready") == [] and is_draft(rig)
    (summary,) = summaries(rig)
    assert "**left as a draft**" in summary and "Kept as a draft because:" in summary and expected in summary
    assert HITL in rig.labels(1)


def test_a_discarded_chore_and_a_failed_review_are_reported_in_the_summary(tmp_path: Path) -> None:
    """Given a discarded chore and a failed review, when finalize runs, then the summary reports both as evidence."""
    rig, head = delivered_rig(tmp_path)

    rig.run(finalizing(rig, head, ReviewFailed("no diff"), chore=ChoreDiscarded("the tests fail", ("old bug",))))

    (summary,) = summaries(rig)
    assert "Chore: discarded (the tests fail)" in summary and "- out of scope: old bug" in summary
    assert "Review: not completed (no diff)" in summary


def test_a_blocked_finalization_rerun_posts_no_second_summary_and_writes_no_second_label(tmp_path: Path) -> None:
    """Given a finalization that kept the draft, when finalize runs again, then neither the summary nor the label is written twice."""
    rig, head = delivered_rig(tmp_path)
    workflow = finalizing(rig, head, None)
    rig.run(workflow)
    writes = len(rig.gh.writes())

    rig.run(workflow)

    assert len(rig.gh.writes()) == writes and len(summaries(rig)) == 1
    assert rig.labels(1).count(HITL) == 1


def test_without_a_label_a_blocked_pull_request_stays_a_draft_and_nothing_is_labelled(tmp_path: Path) -> None:
    """Given no label policy, when a blocker exists, then the summary is posted and no label is written."""
    rig, head = delivered_rig(tmp_path)

    rig.run(finalizing(rig, head, None, label=None))

    assert len(summaries(rig)) == 1 and HITL not in rig.labels(1)
    assert rig.gh.calls_matching("issue", "edit") == []


def test_a_spec_without_an_open_pull_request_is_not_ready(tmp_path: Path) -> None:
    """Given a pull request that is no longer open, when finalize runs, then it reports the blocker instead of marking anything ready."""
    rig, head = delivered_rig(tmp_path)
    rig.gh._prs.clear()

    rig.run(finalizing(rig, head, clean_review(head)))

    (finalized,) = rig.history(Finalized)
    assert finalized.ready is False and finalized.pull_request is None
    assert "there is no open pull request to mark ready" in finalized.blockers
    assert rig.gh.calls_matching("pr", "ready") == []

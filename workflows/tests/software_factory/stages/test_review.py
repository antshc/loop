"""The review Stage: a read-only reviewer on the checkout, run as a one-Stage Workflow."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tests.runtime.harness import Repo
from workflows.software_factory.events.review import (
    PullRequestReviewed,
    ReviewAxis,
    ReviewFailed,
    ReviewFinding,
)
from workflows.software_factory.events.tickets import Delivery
from workflows.software_factory.stages import review
from workflows.tests.software_factory.stages.support import (
    PULL_REQUEST,
    DeveloperScript,
    StageRig,
    answers,
)

AXES = ("bugs", "quality", "smells", "initiative")


def reviewed(
    head: str,
    *,
    outcomes: dict[str, str] | None = None,
    findings: tuple[dict, ...] = (),
    reviewed_head: str | None = None,
):
    """A reviewer action that completes every axis (or the given `outcomes`) and reports `findings` for `reviewed_head`."""
    states = {axis: "completed" for axis in AXES} | (outcomes or {})
    return answers(
        "completed",
        axes=[
            {"name": axis, "outcome": outcome, "note": ""}
            for axis, outcome in states.items()
        ],
        findings=list(findings),
        posted=["comment-1"],
        head=reviewed_head or head,
    )


def reviewing(rig: StageRig, script: DeveloperScript, head: str):
    """The routing Stage, the delivery of `head`, then the review Stage on the checkout."""
    return rig.in_worktree(
        script,
        review.review_pull_request(),
        seeds=[Delivery(head, ("abc1234 ccode(Checkout|10): implement 0",))],
    )


def test_a_completed_review_carries_the_axes_the_findings_and_the_reviewed_head_and_leaves_the_repository_unchanged(
    tmp_path: Path,
) -> None:
    """Given a reviewer that completes every axis with one finding, when the review runs, then the Event carries them and the repository is unchanged."""
    rig = StageRig(tmp_path)
    head = rig.deliver()
    finding = {"axis": "quality", "kind": "quality", "summary": "name is vague"}
    script = DeveloperScript(rig.repo, [reviewed(head, findings=(finding,))])

    result = rig.run(reviewing(rig, script, head))

    assert result.status == "completed"
    assert rig.history(PullRequestReviewed) == (
        PullRequestReviewed(
            tuple(ReviewAxis(axis, "completed") for axis in AXES),
            (ReviewFinding("quality", "name is vague", "quality"),),
            ("comment-1",),
            head,
        ),
    )
    assert rig.repo.head() == head and rig.repo.status() == ""
    prompt = script.requests[0].prompt
    assert PULL_REQUEST in prompt and head in prompt and "Checkout" in prompt


def test_a_review_that_did_not_finish_every_axis_still_passes_so_finalization_can_judge_it(
    tmp_path: Path,
) -> None:
    """Given a reviewer that leaves an axis incomplete, when the review runs, then the Stage passes and the Event shows the incomplete axis."""
    rig = StageRig(tmp_path)
    head = rig.deliver()
    script = DeveloperScript(rig.repo, [reviewed(head, outcomes={"smells": "skipped"})])

    result = rig.run(reviewing(rig, script, head))

    (event,) = rig.history(PullRequestReviewed)
    assert result.status == "completed" and ("smells", "skipped") in [
        (axis.name, axis.outcome) for axis in event.axes
    ]


def test_a_declining_reviewer_fails_the_stage_with_a_review_failed_event(
    tmp_path: Path,
) -> None:
    """Given a reviewer that declines, when the review runs, then the Stage fails and the reason is kept as evidence."""
    rig = StageRig(tmp_path)
    head = rig.deliver()
    script = DeveloperScript(
        rig.repo, [answers("failed", reason="could not load the diff")]
    )

    result = rig.run(reviewing(rig, script, head))

    assert result.status == "failed"
    assert rig.history(ReviewFailed) == (ReviewFailed("could not load the diff"),)
    assert rig.history(PullRequestReviewed) == ()


def test_a_reviewer_that_changes_the_working_tree_fails_the_stage_and_the_checkout_is_restored(
    tmp_path: Path,
) -> None:
    """Given a reviewer that edits a file, when the review runs, then the read-only contract fails it and the checkout is clean."""
    rig = StageRig(tmp_path)
    head = rig.deliver()

    def edits(repo: Repo) -> str:
        repo.write("notes.txt", "review notes\n")
        return reviewed(head)(repo)

    result = rig.run(reviewing(rig, DeveloperScript(rig.repo, [edits]), head))

    assert result.status == "failed"
    assert (
        "read-only attempt changed the working tree"
        in rig.spans.attempts("review-pull-request")[0].attributes[
            "factory.stage.reason"
        ]
    )
    assert (
        rig.repo.head() == head
        and rig.repo.status() == ""
        and rig.history(PullRequestReviewed) == ()
    )


def test_a_target_that_does_not_review_skips_the_review(tmp_path: Path) -> None:
    """Given a routed target with review disabled, when the review Stage runs, then it is skipped and produces no Event."""
    rig = StageRig(tmp_path)
    head = rig.deliver()
    rig.target = replace(rig.target, review=False)
    script = DeveloperScript(rig.repo, [reviewed(head)])

    result = rig.run(reviewing(rig, script, head))

    assert (
        result.status == "completed"
        and rig.skipped() == ["review-pull-request"]
        and script.requests == []
    )
    assert rig.history(PullRequestReviewed) == () and rig.history(ReviewFailed) == ()


def test_a_branch_without_a_pull_request_skips_the_review(tmp_path: Path) -> None:
    """Given nothing published, when the review Stage runs, then it is skipped."""
    rig = StageRig(tmp_path)
    script = DeveloperScript(rig.repo, [reviewed("0" * 40)])

    result = rig.run(reviewing(rig, script, "0" * 40))

    assert (
        result.status == "completed"
        and rig.skipped() == ["review-pull-request"]
        and script.requests == []
    )

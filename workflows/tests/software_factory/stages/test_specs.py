"""The Spec Stages, each run through the runtime as a one-Stage Workflow against the shared `gh` fake and real Git."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflows.software_factory.events.pull_requests import DraftPullRequestPublished
from workflows.software_factory.events.specs import (
    CheckoutPrepared, DeliveryAnnounced, RepositoryTarget, SpecRouted, SpecWork, WorkFound,
)
from loop import DefinitionError, Stage, Workflow, handoff
from loop.shared.github.issues.errors import IssueError
from workflows.software_factory.stages import specs
from tests.shared.fakes import issue
from workflows.tests.software_factory.stages.support import BRANCH, HITL, StageRig, probe


def test_a_spec_whose_target_label_matches_a_configured_repository_is_routed_with_its_target(tmp_path: Path) -> None:
    """Given a target label that matches a configured repository in another case, when the Spec runs, then the routed target is emitted."""
    rig = StageRig(tmp_path, labels=("spec", "repo:target:OWNER/Repo", "repo:base:main"))
    seen: list[SpecRouted] = []

    result = rig.run(rig.workflow(probe(SpecRouted, seen)))

    assert result.status == "completed"
    assert seen == [SpecRouted(rig.target, "Checkout", "main", BRANCH)]
    assert seen[0].subject_prefix(10) == "ccode(Checkout|10): "


@pytest.mark.parametrize(
    "labels",
    [
        ("spec", "repo:base:main"),
        ("spec", "repo:target:not-a-slug", "repo:base:main"),
        ("spec", "repo:target:owner/repo", "repo:target:other/one", "repo:base:main"),
        ("spec", "repo:target:acme/widgets", "repo:base:main"),
        ("spec", "repo:target:owner/repo"),
    ],
    ids=["no-target", "malformed-target", "duplicated-target", "unconfigured-target", "no-base"],
)
def test_a_spec_that_cannot_be_routed_holds_without_fetching_or_opening_a_worktree(tmp_path: Path, labels: tuple[str, ...]) -> None:
    """Given a Spec whose labels name no configured target, when it runs, then it holds before any fetch or worktree."""
    rig = StageRig(tmp_path, labels=labels)

    result = rig.run(rig.workflow(specs.prepare_checkout()))

    assert result.status == "held"
    assert rig.runner.calls == []
    assert not rig.target.worktrees.exists()


def test_a_spec_that_cannot_be_routed_is_blocked_with_the_reason_when_it_has_work(tmp_path: Path) -> None:
    """Given an unroutable Spec with an actionable Ticket, when block_spec is its hold route, then it is labelled and says why."""
    rig = StageRig(tmp_path, labels=("spec", "repo:target:acme/widgets", "repo:base:main"))
    workflow = Workflow("t").context(SpecWork).stage(rig.route().on_hold(handoff(specs.block_spec(label=HITL))))

    result = rig.run(workflow)

    assert result.status == "handed_off"
    assert rig.labels(1) == ["spec", "repo:target:acme/widgets", "repo:base:main", HITL]
    assert rig.comments(1) == ["implement_spec: repo:target:acme/widgets is not a configured repository"]


def test_a_spec_without_an_actionable_ticket_is_not_labelled_when_it_is_blocked(tmp_path: Path) -> None:
    """Given an unroutable Spec whose only Ticket is held, when block_spec runs, then nothing is written to the tracker."""
    rig = StageRig(tmp_path, labels=("spec", "repo:base:main"), tickets=[issue(10, "Add login form", labels=(HITL,))])
    workflow = Workflow("t").context(SpecWork).stage(rig.route().on_hold(handoff(specs.block_spec(label=HITL))))

    rig.run(workflow)

    assert rig.gh.writes() == []


def test_the_prepare_checkout_stage_passes_after_fetching_when_the_base_branch_exists_on_origin(tmp_path: Path) -> None:
    """Given the base branch exists on origin, when the checkout is prepared, then it fetches and passes."""
    rig = StageRig(tmp_path)
    seen: list[CheckoutPrepared] = []

    result = rig.run(rig.workflow(specs.prepare_checkout(), probe(CheckoutPrepared, seen)))

    assert result.status == "completed"
    assert rig.runner.git("fetch") != []
    assert seen == [CheckoutPrepared("main")]


def test_the_prepare_checkout_stage_holds_when_the_base_branch_is_missing_on_origin(tmp_path: Path) -> None:
    """Given the base branch is missing on origin, when the checkout is prepared, then it holds."""
    rig = StageRig(tmp_path, labels=("spec", "repo:target:owner/repo", "repo:base:release/9"))

    result = rig.run(rig.workflow(specs.prepare_checkout()))

    assert result.status == "held"
    assert result.reason == "target branch 'release/9' does not exist on owner/repo"


def test_the_hand_to_human_stage_labels_and_comments_once_however_often_it_runs(tmp_path: Path) -> None:
    """Given a held checkout, when hand_to_human runs twice for the Spec, then its label and comment exist exactly once."""
    rig = StageRig(tmp_path, labels=("spec", "repo:target:owner/repo", "repo:base:release/9"))
    workflow = rig.workflow(specs.prepare_checkout().on_hold(handoff(specs.hand_to_human(label=HITL))))

    first, second = rig.run(workflow), rig.run(workflow)

    assert (first.status, second.status) == ("handed_off", "handed_off")
    assert rig.labels(1).count(HITL) == 1
    assert rig.comments(1) == ["implement_spec: target branch 'release/9' does not exist on owner/repo"]
    assert len(rig.gh.calls_matching("issue", "edit")) == 1


def test_the_hand_to_human_stage_adopts_a_hand_off_a_crashed_run_already_made(tmp_path: Path) -> None:
    """Given the Spec already carries the label, when hand_to_human runs, then it writes nothing."""
    rig = StageRig(tmp_path, labels=("spec", "repo:target:owner/repo", "repo:base:release/9", HITL))
    workflow = rig.workflow(specs.prepare_checkout().on_hold(handoff(specs.hand_to_human(label=HITL))))

    rig.run(workflow)

    assert rig.gh.writes() == []


def test_a_failure_is_commented_on_the_spec_once_however_often_the_run_fails(tmp_path: Path) -> None:
    """Given a Stage that raises a declared error, when the Workflow runs twice, then the failure comment exists exactly once."""
    rig = StageRig(tmp_path)

    def fail(_: object) -> None:
        raise IssueError("tracker unavailable")

    broken = Stage("broken", specs.SpecInput, produces=CheckoutPrepared).action(fail)
    workflow = rig.workflow(broken).on_error((IssueError,), handoff(specs.comment_failure()))

    first, second = rig.run(workflow), rig.run(workflow)

    assert (first.status, second.status) == ("handed_off", "handed_off")
    assert rig.comments(1) == ["implement_spec: the Spec's run failed; the run's ledger and spans hold the details"]


def test_the_require_work_stage_holds_when_no_ticket_is_actionable_and_passes_otherwise(tmp_path: Path) -> None:
    """Given one Spec with only a held Ticket and one with an actionable Ticket, when require_work runs, then it holds and passes."""
    held = StageRig(tmp_path / "held", tickets=[issue(10, "Add login form", labels=(HITL,))])
    ready = StageRig(tmp_path / "ready")
    seen: list[WorkFound] = []

    assert held.run(held.workflow(specs.require_work())).status == "held"
    assert ready.run(ready.workflow(specs.require_work(), probe(WorkFound, seen))).status == "completed"
    assert seen == [WorkFound((10,))]


def published(url: str | None) -> Stage[specs.SpecInput, None, DraftPullRequestPublished]:
    """A stand-in for the publication Stage."""
    return Stage("publish-draft-pr", specs.SpecInput, produces=DraftPullRequestPublished).action(
        lambda _: DraftPullRequestPublished(url, BRANCH, pushed=True),
    )


def test_the_announce_delivered_stage_comments_the_pull_request_on_the_spec_once(tmp_path: Path) -> None:
    """Given a published draft pull request, when delivery is announced twice, then the Spec carries one comment naming it."""
    rig = StageRig(tmp_path)
    seen: list[DeliveryAnnounced] = []
    workflow = rig.workflow(published("https://example.test/pull/7"), specs.announce_delivered(), probe(DeliveryAnnounced, seen))

    rig.run(workflow)
    rig.run(workflow)

    assert rig.comments(1) == ["implement_spec: all Tickets delivered; draft pull request: https://example.test/pull/7"]
    assert seen[0] == DeliveryAnnounced("https://example.test/pull/7")


def test_the_announce_delivered_stage_writes_nothing_without_a_pull_request(tmp_path: Path) -> None:
    """Given nothing was published, when delivery is announced, then the Spec gets no comment."""
    rig = StageRig(tmp_path)

    rig.run(rig.workflow(published(None), specs.announce_delivered()))

    assert rig.gh.writes() == []


def test_a_repository_configured_twice_is_rejected_when_the_stage_is_built(tmp_path: Path) -> None:
    """Given two targets with the same slug in different case, when the routing Stage is built, then it is rejected."""
    target = RepositoryTarget("owner/repo", tmp_path, tmp_path)

    with pytest.raises(ValueError, match="duplicate repository target: owner/repo"):
        specs.route_spec([target, RepositoryTarget("OWNER/repo", tmp_path, tmp_path)])


def test_a_stage_that_reads_the_routed_target_is_rejected_without_the_routing_stage() -> None:
    """Given no routing Stage, when prepare_checkout is the first Stage, then the definition is rejected."""
    with pytest.raises(DefinitionError, match="no earlier Stage produces SpecRouted"):
        Workflow("t").context(SpecWork).stage(specs.prepare_checkout())

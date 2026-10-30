"""The publication Stage against a real temporary repository with a local bare remote and the shared `gh` fake."""

from __future__ import annotations

from pathlib import Path

from workflows.software_factory.events.pull_requests import DraftPullRequestPublished
from workflows.software_factory.events.tickets import TicketClosed
from workflows.software_factory.stages import publish
from tests.runtime.harness import command
from tests.shared.fakes import issue
from workflows.tests.software_factory.stages.support import BRANCH, StageRig, never_closes, probe, seeded


def pushes(rig: StageRig) -> list[tuple[str, ...]]:
    """Every `git push` the rig's Git client ran."""
    return rig.runner.git("push")


def test_a_branch_that_is_ahead_is_pushed_and_gets_exactly_one_draft_pull_request(tmp_path: Path) -> None:
    """Given a feature branch ahead of origin, when it is published, then it is pushed and one draft pull request is created."""
    rig = StageRig(tmp_path)
    head = rig.branch_off_main(BRANCH)
    seen: list[DraftPullRequestPublished] = []

    result = rig.run(rig.workflow(never_closes(), publish.publish_draft_pr(), probe(DraftPullRequestPublished, seen)))

    assert result.status == "completed"
    assert rig.remote_head(BRANCH) == head
    creates = rig.gh.calls_matching("pr", "create")
    assert len(creates) == 1 and "--draft" in creates[0]
    assert creates[0][creates[0].index("--title") + 1] == "Checkout: Add login page"
    assert creates[0][creates[0].index("--body") + 1].splitlines()[0] == "Checkout"
    assert seen == [DraftPullRequestPublished("https://github.com/owner/repo/pull/99", BRANCH, pushed=True)]


def test_a_second_run_after_a_complete_publication_pushes_and_creates_nothing(tmp_path: Path) -> None:
    """Given a published branch, when the Stage runs again, then no second push or pull request happens."""
    rig = StageRig(tmp_path)
    rig.branch_off_main(BRANCH)
    workflow = rig.workflow(never_closes(), publish.publish_draft_pr())
    rig.run(workflow)

    rig.run(workflow)

    assert len(pushes(rig)) == 1
    assert len(rig.gh.calls_matching("pr", "create")) == 1


def test_a_run_after_a_crash_between_the_push_and_the_pull_request_creates_it_once_without_pushing_again(tmp_path: Path) -> None:
    """Given a branch pushed by a crashed run that never opened its pull request, when the Stage runs, then it opens exactly one."""
    rig = StageRig(tmp_path)
    rig.branch_off_main(BRANCH)
    command(("push", "-q", "origin", BRANCH), rig.repo.path)
    rig.git.fetch(rig.repo.path)

    result = rig.run(rig.workflow(never_closes(), publish.publish_draft_pr()))

    assert result.status == "completed"
    assert pushes(rig) == []
    assert len(rig.gh.calls_matching("pr", "create")) == 1


def test_a_new_commit_after_publication_is_pushed_without_force_and_the_same_pull_request_is_kept(tmp_path: Path) -> None:
    """Given a published branch with a new commit, when the Stage runs, then it fast-forwards the remote and creates no second pull request."""
    rig = StageRig(tmp_path)
    rig.branch_off_main(BRANCH)
    workflow = rig.workflow(never_closes(), publish.publish_draft_pr())
    rig.run(workflow)
    command(("checkout", "-q", BRANCH), rig.repo.path)
    rig.repo.write("more.txt", "more\n")
    head = rig.repo.commit("more work")
    command(("checkout", "-q", "main"), rig.repo.path)

    rig.run(workflow)

    assert rig.remote_head(BRANCH) == head
    assert len(rig.gh.calls_matching("pr", "create")) == 1
    assert all(not {"--force", "-f", "--force-with-lease"} & set(call) for call in pushes(rig))


def test_a_pull_request_with_a_stale_description_is_updated_not_duplicated(tmp_path: Path) -> None:
    """Given a pull request whose description predates a closed Ticket, when the Stage runs, then it edits the description."""
    rig = StageRig(tmp_path, tickets=[issue(10, "Add login form"), issue(11, "Add login tests")])
    rig.branch_off_main(BRANCH)
    workflow = rig.workflow(never_closes(), publish.publish_draft_pr())
    rig.run(workflow)
    rig.gh.close_issue(10)

    rig.run(workflow)

    assert len(rig.gh.calls_matching("pr", "create")) == 1
    (edit,) = rig.gh.calls_matching("pr", "edit")
    assert "#10" not in edit[edit.index("--body") + 1] and "- [ ] #11 Add login tests" in edit[edit.index("--body") + 1]


def test_tickets_this_work_item_closed_are_listed_as_delivered_in_number_order(tmp_path: Path) -> None:
    """Given a Ticket the Workflow closed, which the tracker's Spec read no longer lists, when the Stage publishes, then the description shows it delivered before the open one."""
    rig = StageRig(tmp_path, tickets=[issue(11, "Add login tests")])
    rig.branch_off_main(BRANCH)

    rig.run(rig.workflow(seeded(TicketClosed(10, "abc1234", "Add login form")), publish.publish_draft_pr()))

    (create,) = rig.gh.calls_matching("pr", "create")
    assert create[create.index("--body") + 1].splitlines()[-2:] == ["- [x] #10 Add login form", "- [ ] #11 Add login tests"]


def test_a_spec_with_nothing_to_publish_passes_without_a_pull_request(tmp_path: Path) -> None:
    """Given no feature branch yet, when the Stage runs, then it passes with no pull request and writes nothing."""
    rig = StageRig(tmp_path)
    seen: list[DraftPullRequestPublished] = []

    result = rig.run(rig.workflow(never_closes(), publish.publish_draft_pr(), probe(DraftPullRequestPublished, seen)))

    assert result.status == "completed"
    assert seen == [DraftPullRequestPublished(None, BRANCH, pushed=False)]
    assert rig.gh.writes() == [] and pushes(rig) == []

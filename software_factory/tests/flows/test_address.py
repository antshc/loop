"""The address-feedback fragment, placed in a minimal definition over a real repository with a local bare remote, the shared pull request fake and scripted address agents."""

from __future__ import annotations

from pathlib import Path

import pytest
from loop import WorktreeError

from software_factory.events.feedback import FeedbackBatch, FeedbackHandled, IssueResolved, IssueUnclear
from software_factory.flows.address import address_feedback
from software_factory.runtime import Flow, Workflow, handoff, reroute
from software_factory.runtime.nodes import StageNode, WithinNode
from software_factory.stages import feedback
from software_factory.stages.address import IssueWork
from tests.runtime.harness import command
from tests.shared.fakes import check_run, issue_comment
from tests.stages.pr_support import (
    BRANCH, TRUST, Action, AddressAgent, Crash, Elsewhere, PrRig, PrWork, Verifier, answer, blocking, reply_text,
    selected_batch, thread,
)


def subject(work: IssueWork) -> str:
    return f"address {work.issue.id} (PR #{work.polled.number})"


def placed(rig: PrRig, verifier: Verifier, **routes) -> Workflow[PrWork]:
    return rig.workflow(address_feedback(message=subject, verify=verifier, **routes))


def untouched(rig: PrRig) -> bool:
    """Nothing was committed, pushed or replied."""
    return rig.subjects() == [] and rig.remote_head(BRANCH) == rig.head and rig.replies() == []


def crash(cwd: Path, request: dict) -> dict:
    raise Crash("the agent died")


def test_a_dirty_worktree_at_batch_start_ends_through_the_stuck_route_without_running_any_agent(tmp_path: Path) -> None:
    """Given a recovery worktree with an unrelated edit, when the fragment runs, then the stuck hand-off runs, neither a check nor an agent does, and Loop preserves the dirty worktree."""
    rig = PrRig(tmp_path)
    rig.leave_untracked_edit()
    agent, verifier, blocked = AddressAgent({"i1": answer("fix-needed", edit="cart.py")}), Verifier(), []

    with pytest.raises(WorktreeError, match="preserved"):
        rig.run(placed(rig, verifier, on_stuck=handoff(blocking(blocked))), agent)

    assert blocked == ["blocked"]
    assert agent.requests == [] and verifier.paths == []
    assert (rig.worktree() / "junk.txt").exists() and untouched(rig)


def test_a_dirty_worktree_stops_failed_when_no_stuck_route_is_given(tmp_path: Path) -> None:
    """Given a dirty recovery worktree and the default routes, when the fragment runs, then no agent runs and the scope ends failed."""
    rig = PrRig(tmp_path)
    rig.leave_untracked_edit()
    agent = AddressAgent({})

    with pytest.raises(WorktreeError, match="preserved"):
        rig.run(placed(rig, Verifier()), agent)

    assert agent.requests == []
    assert [span.attributes.get("factory.scope.status") for span in rig.spans.roots[0].children] == ["failed"]


def test_a_failed_baseline_with_green_ci_ends_through_the_stuck_route(tmp_path: Path) -> None:
    """Given a failing baseline check and green CI, when the fragment runs, then the stuck hand-off runs and no agent does."""
    rig = PrRig(tmp_path)
    agent, verifier, blocked = AddressAgent({"i1": answer("fix-needed", edit="cart.py")}), Verifier(fail=lambda _: True), []

    result = rig.run(placed(rig, verifier, on_stuck=handoff(blocking(blocked))), agent)

    assert result.status == "handed_off" and blocked == ["blocked"]
    assert len(verifier.paths) == 1 and agent.requests == []
    assert untouched(rig)


def test_a_failed_baseline_while_ci_is_failing_takes_the_red_baseline_route(tmp_path: Path) -> None:
    """Given a failing baseline and failing CI, when the fragment sits in a dispatch case, then the red-baseline route reroutes to the sibling case and the stuck route is not taken."""
    rig = PrRig(tmp_path)
    rig.pull.check_runs = [check_run(1, "test", conclusion="failure")]
    agent, verifier, blocked, rerouted = AddressAgent({"i1": answer("fix-needed", edit="cart.py")}), Verifier(fail=lambda _: True), [], []
    fragment = address_feedback(message=subject, verify=verifier, on_baseline_red=reroute(Elsewhere), on_stuck=handoff(blocking(blocked)))
    workflow = (
        Workflow("address-once").context(PrWork)
        .stage(rig.poll())
        .stage(feedback.select(trust=TRUST))
        .dispatch(Action, {FeedbackBatch: fragment, Elsewhere: Flow("elsewhere").stage(blocking(rerouted).named("maintain-ci"))})
    )

    result = rig.run(workflow, agent)

    assert result.status == "completed"
    assert rerouted == ["blocked"] and blocked == [] and agent.requests == []
    assert untouched(rig)


@pytest.mark.parametrize(
    "grouping",
    [
        lambda items: [{"id": "a", "statement": "s", "keys": [items[0]["key"]]}],
        lambda items: [
            {"id": "a", "statement": "s", "keys": [item["key"] for item in items]},
            {"id": "b", "statement": "s", "keys": [items[0]["key"]]},
        ],
    ],
    ids=["an item left uncovered", "an item covered twice"],
)
def test_a_grouping_that_leaves_an_item_uncovered_or_covers_it_twice_fails_group_feedback(tmp_path: Path, grouping) -> None:
    """Given two feedback items, when the grouping agent covers one item not at all or twice, then group-feedback fails and no issue is resolved."""
    rig = PrRig(tmp_path, threads=[thread("t1", "One"), thread("t2", "Two")])
    agent = AddressAgent({}, grouping=grouping)

    result = rig.run(placed(rig, Verifier()), agent)

    assert result.status == "failed"
    assert agent.modes() == ["group"]
    assert untouched(rig)


def test_an_issue_that_claims_a_fix_but_changed_nothing_fails_the_attempt(tmp_path: Path) -> None:
    """Given a resolve agent that claims a fix without editing, when the fragment runs, then the issue attempt fails and nothing is committed, pushed or replied."""
    rig = PrRig(tmp_path)

    result = rig.run(placed(rig, Verifier()), AddressAgent({"i1": answer("fix-needed")}))

    assert result.status == "failed" and "claimed a fix" in (result.reason or "")
    assert untouched(rig)


def test_an_issue_that_reports_no_change_but_changed_files_fails_and_the_worktree_is_retained(tmp_path: Path) -> None:
    """Given a resolve agent that edits while reporting no change needed, when the fragment runs, then the attempt fails, nothing is committed and the edit stays in the retained worktree."""
    rig = PrRig(tmp_path)

    with pytest.raises(WorktreeError, match="preserved"):
        rig.run(placed(rig, Verifier()), AddressAgent({"i1": answer("no-change-needed", edit="cart.py")}))

    assert (rig.worktree() / "cart.py").read_text() == "cart.py\n"
    assert untouched(rig)


def test_an_issue_without_path_and_line_evidence_fails(tmp_path: Path) -> None:
    """Given a resolve agent that cites no path:line evidence, when the fragment runs, then the issue attempt fails."""
    rig = PrRig(tmp_path)

    result = rig.run(placed(rig, Verifier()), AddressAgent({"i1": answer("no-change-needed", evidence=("no citation",))}))

    assert result.status == "failed" and "path:line evidence" in (result.reason or "")


def test_a_failed_issue_ends_through_the_stuck_route(tmp_path: Path) -> None:
    """Given a stuck hand-off, when an issue attempt fails, then the run ends handed off once and nothing is published."""
    rig = PrRig(tmp_path)
    blocked: list[str] = []

    result = rig.run(
        placed(rig, Verifier(), on_stuck=handoff(blocking(blocked))), AddressAgent({"i1": answer("fix-needed")}),
    )

    assert result.status == "handed_off" and blocked == ["blocked"]
    assert untouched(rig)


def test_a_fix_that_passes_verification_is_committed_once_with_the_given_subject(tmp_path: Path) -> None:
    """Given a fix that passes verification, when the fragment runs, then exactly one commit with the given subject carries it."""
    rig = PrRig(tmp_path)
    verifier = Verifier()

    result = rig.run(placed(rig, verifier), AddressAgent({"i1": answer("fix-needed", edit="fix.txt")}))

    assert result.status == "completed"
    assert rig.subjects() == ["address i1 (PR #7)"]
    assert command(("show", "--stat", "--format=", rig.recovery_branch()), rig.repo.path).count("fix.txt") == 1
    assert len(verifier.paths) == 2 and set(verifier.paths) == {rig.worktree()}


def test_a_fix_that_fails_verification_commits_nothing_and_retains_the_worktree(tmp_path: Path) -> None:
    """Given a fix whose verification fails, when the fragment runs, then nothing is committed, replied or pushed and the edit stays in the worktree."""
    rig = PrRig(tmp_path)
    verifier = Verifier(fail=lambda path: (path / "fix.txt").exists())

    with pytest.raises(WorktreeError, match="preserved"):
        rig.run(placed(rig, verifier), AddressAgent({"i1": answer("fix-needed", edit="fix.txt")}))

    assert (rig.worktree() / "fix.txt").exists()
    assert untouched(rig)


def test_an_unclear_issue_keeps_its_replies_resolves_the_next_issue_publishes_and_ends_asking_for_clarification(tmp_path: Path) -> None:
    """Given one unclear and one fixable issue, when the fragment runs, then the fix is committed, both items are replied to, the branch is pushed and the run ends with the reviewer clarification request."""
    rig = PrRig(tmp_path, threads=[thread("t1", "What do you mean?"), thread("t2", "Handle the empty cart")])
    agent = AddressAgent({"i1": answer("unclear", reason="which cart?"), "i2": answer("fix-needed", edit="fix.txt")})

    result = rig.run(placed(rig, Verifier()), agent)

    assert (result.status, result.reason) == ("failed", "Reviewer clarification requested")
    assert agent.modes() == ["group", "resolve:i1", "resolve:i2"]
    assert rig.subjects() == ["address i2 (PR #7)"]
    assert rig.replies() == [reply_text("thread:t1:t1c"), reply_text("thread:t2:t2c")]
    assert rig.remote_head(BRANCH) == command(("rev-parse", rig.recovery_branch()), rig.repo.path).strip()
    assert [event.issue for event in rig.history(IssueUnclear)] == ["i1"]
    assert [event.issue for event in rig.history(IssueResolved)] == ["i2"]


def test_an_unclear_batch_takes_the_stuck_route_after_publishing(tmp_path: Path) -> None:
    """Given an unclear issue and a stuck hand-off, when the fragment runs, then the replies are posted first and the run then ends handed off."""
    rig = PrRig(tmp_path)
    blocked: list[str] = []

    result = rig.run(
        placed(rig, Verifier(), on_stuck=handoff(blocking(blocked))), AddressAgent({"i1": answer("unclear")}),
    )

    assert (result.status, result.reason) == ("handed_off", "Reviewer clarification requested") and blocked == ["blocked"]
    assert rig.replies() == [reply_text("thread:t1:t1c")]


def test_a_batch_is_pushed_without_force_and_every_feedback_item_gets_exactly_one_reply(tmp_path: Path) -> None:
    """Given two threads and a Comment, when the fragment runs, then the push is not forced, the remote head equals the local head and each item is replied to once."""
    rig = PrRig(tmp_path, threads=[thread("t1", "One"), thread("t2", "Two")])
    rig.pull.comments = [issue_comment(501, "reviewer", "Please add docs")]
    keys = selected_batch(rig).keys
    agent = AddressAgent({"i1": answer("fix-needed", edit="one.txt"), "i2": answer("no-change-needed"), "i3": answer("no-change-needed")})

    result = rig.run(placed(rig, Verifier()), agent)

    assert result.status == "completed"
    pushes = rig.runner.git("push")
    assert pushes == [("git", "push", "origin", f"HEAD:refs/heads/{BRANCH}")]
    assert rig.remote_head(BRANCH) == command(("rev-parse", rig.recovery_branch()), rig.repo.path).strip() != rig.head
    thread_replies = [comment["body"] for pull_thread in rig.pull.threads for comment in pull_thread["comments"][1:]]
    assert thread_replies == [reply_text(keys[0]), reply_text(keys[1])]
    assert [comment["body"] for comment in rig.pull.comments[1:]] == [
        f"In response to https://github.com/a/b/pull/1#issuecomment-501:\n\n{reply_text(keys[2])}"
    ]
    assert rig.history(FeedbackHandled)[0].keys == keys


def test_a_batch_without_commits_pushes_nothing_and_still_replies(tmp_path: Path) -> None:
    """Given an issue that needs no change, when the fragment runs, then nothing is pushed and the item is still replied to."""
    rig = PrRig(tmp_path)

    result = rig.run(placed(rig, Verifier()), AddressAgent({"i1": answer("no-change-needed")}))

    assert result.status == "completed"
    assert rig.runner.git("push") == [] and rig.remote_head(BRANCH) == rig.head
    assert rig.replies() == [reply_text("thread:t1:t1c")]


def test_a_batch_resumed_after_a_crash_in_issue_three_of_five_replays_the_finished_issues(tmp_path: Path) -> None:
    """Given a crash in issue 3 of 5, when the fragment runs again, then issues 1 and 2 are replayed without asking the agent, grouping is not repeated and each item is replied to once."""
    rig = PrRig(tmp_path, threads=[thread(f"t{n}", f"Concern {n}") for n in range(1, 6)])
    fixes = {f"i{n}": answer("fix-needed", edit=f"fix{n}.txt") for n in range(1, 6)}
    first = AddressAgent({**fixes, "i3": crash})
    second = AddressAgent(fixes)

    with pytest.raises(Crash):
        rig.run(placed(rig, Verifier()), first)
    interrupted = rig.subjects()
    result = rig.run(placed(rig, Verifier()), second)

    assert first.modes() == ["group", "resolve:i1", "resolve:i2", "resolve:i3"]
    assert interrupted == ["address i1 (PR #7)", "address i2 (PR #7)"]
    assert second.modes() == ["resolve:i3", "resolve:i4", "resolve:i5"]
    assert rig.skipped() == ["group-feedback", "resolve-issue", "resolve-issue"]
    assert result.status == "completed"
    assert rig.subjects() == [f"address i{n} (PR #7)" for n in range(1, 6)]
    assert sorted(rig.replies()) == sorted(reply_text(f"thread:t{n}:t{n}c") for n in range(1, 6))


def test_two_definitions_place_the_fragment_with_their_own_subjects_and_verify_callables(tmp_path: Path) -> None:
    """Given two definitions with different subjects and verify callables, when each runs, then each uses its own values and both share the same Stage logic."""
    first, second = PrRig(tmp_path / "a"), PrRig(tmp_path / "b")
    verify_a, verify_b = Verifier(), Verifier()
    fragment_a = address_feedback(message=lambda work: f"alpha {work.issue.id}", verify=verify_a)
    fragment_b = address_feedback(message=lambda work: f"beta {work.issue.id}", verify=verify_b)
    agent = lambda: AddressAgent({"i1": answer("fix-needed", edit="fix.txt")})

    first.run(first.workflow(fragment_a), agent())
    second.run(second.workflow(fragment_b), agent())

    assert first.subjects() == ["alpha i1"] and second.subjects() == ["beta i1"]
    assert len(verify_a.paths) == 2 and len(verify_b.paths) == 2
    assert verify_a.paths != verify_b.paths
    assert [stage.judge for stage in _stages(fragment_a)] == [stage.judge for stage in _stages(fragment_b)]


def _stages(fragment: Flow) -> list:
    """The Stages of the fragment's batch scope and its issue loop, in order."""
    (within,) = [node for node in fragment.nodes if isinstance(node, WithinNode)]
    stages = [node.stage for node in within.body.nodes if isinstance(node, StageNode)]
    loop = next(node for node in within.body.nodes if not isinstance(node, StageNode))
    return [*stages, *(node.stage for node in loop.body.nodes)]

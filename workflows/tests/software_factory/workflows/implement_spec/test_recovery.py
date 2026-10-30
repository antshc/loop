"""Interruption, a killed process and the release of held work: how a restarted implement_spec run converges."""

from __future__ import annotations

from pathlib import Path

import pytest

from .harness import TWO_TICKETS, Died, ImplementSpecHarness


def test_an_interrupted_run_exits_130_and_a_restart_delivers_the_rest_without_repeating_anything(tmp_path: Path) -> None:
    """Given an interrupt after the first Ticket, when the run is restarted, then finished Tickets are not re-implemented and no pull request or Comment is duplicated."""
    harness = ImplementSpecHarness(tmp_path, tickets=TWO_TICKETS)
    harness.agents.stop.after = 10

    interrupted = harness.run()

    assert interrupted == 130
    assert harness.closed() == [10] and harness.pull_request_creations() == 1
    harness.restart()

    resumed = harness.run()

    assert resumed == 0
    assert harness.closed() == [10, 11] and harness.agents.developer_runs(10) == 1 and harness.agents.developer_runs(11) == 1
    assert harness.pull_request_creations() == 1 and len(harness.rig.gh.calls_matching("pr", "ready")) == 1
    delivered = [body for body in harness.rig.comments(1) if "all Tickets delivered" in body]
    assert len(delivered) == 1


def test_a_finished_spec_run_again_repeats_nothing(tmp_path: Path) -> None:
    """Given a Spec whose every Ticket is delivered, when the entry point runs again, then nothing is implemented, pushed or commented twice."""
    harness = ImplementSpecHarness(tmp_path, tickets=TWO_TICKETS)
    harness.run()
    writes = len(harness.rig.gh.writes())
    harness.restart()

    code = harness.run()

    assert code == 0 and harness.agents.developer_runs() == 2
    assert harness.pull_request_creations() == 1 and len(harness.rig.gh.writes()) <= writes + 1


def test_a_killed_process_leaves_failures_that_a_restart_counts_against_the_same_cap(tmp_path: Path) -> None:
    """Given a first attempt that failed and a process that died during the second, when it restarts, then the cap counts both and the Ticket is escalated without a third run."""
    harness = ImplementSpecHarness(tmp_path)
    harness.agents.actions[10] = ["fail", "die", "commit"]
    with pytest.raises(Died):
        harness.run()
    harness.restart()

    code = harness.run()

    assert code == 1
    assert harness.agents.developer_runs(10) == 2
    assert "hitl" in harness.labels(10) and "hitl" in harness.labels(1)


def test_a_spec_that_still_carries_hitl_starts_no_agent_and_pushes_nothing(tmp_path: Path) -> None:
    """Given an escalated Spec that a person has not released, when the entry point runs again, then no agent runs and nothing is written or pushed."""
    harness = ImplementSpecHarness(tmp_path)
    harness.agents.actions[10] = ["fail"]
    harness.run()
    runs, writes = harness.agents.developer_runs(), len(harness.rig.gh.writes())
    harness.restart()

    code = harness.run()

    assert code == 0
    assert harness.agents.developer_runs() == runs and len(harness.rig.gh.writes()) == writes
    assert harness.rig.runner.git("push") == []


def test_removing_hitl_releases_the_work_and_the_next_run_starts_with_a_fresh_budget(tmp_path: Path) -> None:
    """Given an escalated Ticket whose hitl labels a person removed, when the entry point runs, then it releases the work and the Ticket gets two new attempts."""
    harness = ImplementSpecHarness(tmp_path)
    harness.agents.actions[10] = ["fail"]
    assert harness.run() == 1 and harness.agents.developer_runs(10) == 2
    harness.rig.issues.remove_label(10, "hitl")
    harness.rig.issues.remove_label(1, "hitl")
    harness.restart()

    code = harness.run()

    assert code == 1 and harness.agents.developer_runs(10) == 4
    assert harness.outcomes("implement-ticket") == ["fail", "fail"]
    assert "hitl" in harness.labels(10) and "hitl" in harness.labels(1)


def test_a_released_ticket_that_now_succeeds_is_delivered_and_the_spec_is_made_ready(tmp_path: Path) -> None:
    """Given a released Ticket whose developer now commits, when the entry point runs, then the Ticket is delivered and the pull request is made ready."""
    harness = ImplementSpecHarness(tmp_path)
    harness.agents.actions[10] = ["fail", "fail", "commit"]
    harness.run()
    harness.rig.issues.remove_label(10, "hitl")
    harness.rig.issues.remove_label(1, "hitl")
    harness.restart()

    code = harness.run()

    assert code == 0 and harness.closed() == [10]
    assert harness.rig.gh.calls_matching("pr", "ready")
    status = harness.rig.ledger.status("implement_spec", "spec-1")
    assert status is not None and status.epoch == 2


def test_a_ticket_that_keeps_hitl_while_the_spec_is_released_is_not_implemented_again(tmp_path: Path) -> None:
    """Given a person who removed hitl from the Spec only, when the entry point runs, then the Ticket stays held and no agent runs."""
    harness = ImplementSpecHarness(tmp_path)
    harness.agents.actions[10] = ["fail"]
    harness.run()
    harness.rig.issues.remove_label(1, "hitl")
    harness.restart()

    code = harness.run()

    assert code == 0 and harness.agents.developer_runs(10) == 2
    assert "hitl" in harness.labels(10) and "hitl" not in harness.labels(1)


def test_a_spec_handed_over_by_the_planner_is_planned_again_once_hitl_is_removed(tmp_path: Path) -> None:
    """Given a Spec the planner handed to a person, when hitl is removed, then the next run is released and plans the Spec afresh instead of resuming the hand-off."""
    harness = ImplementSpecHarness(tmp_path, tickets=[])
    assert harness.run() == 1 and "hitl" in harness.labels(1)
    harness.rig.issues.remove_label(1, "hitl")
    harness.agents.planned = [{"title": "Add login form", "body": "Build the form", "blocked_by": []}]
    harness.restart()

    code = harness.run()

    assert code == 0 and len(harness.agents.prompts["planner"]) == 2
    assert harness.closed() == [101]
    status = harness.rig.ledger.status("implement_spec", "spec-1")
    assert status is not None and status.epoch == 2

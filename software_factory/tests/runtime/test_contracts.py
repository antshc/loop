"""Repository contracts, enforced by the runtime after the gate: read-only, agent commits and runtime commits, by outcome."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from software_factory.agents import Declined
from software_factory.runtime import (
    AgentCommits, Evidence, Fail, Flow, HarnessCommits, Hold, NoRepository, Pass, ReadOnly, Restore, Retain, RunResult,
    Stage, Workflow,
)

from .harness import Ask, Done, Rig, TESTER, WorkCtx, committing, declining, editing, idle
from .support import Delivered, NoInput

PREFIX = "ccode(t): "


def attempt(contract: Any, gate: Any = None, *, on_hold: Any = None) -> Stage[Any, Any, Any]:
    """An agent Stage under `contract`; by default it passes with the head it ends on."""
    stage = Stage("work", NoInput, Delivered).agent(TESTER, lambda i, path: Ask("go")).contract(contract)
    stage = stage.gate(gate or (lambda i, reply, evidence: Pass(Delivered(evidence.head))))
    return stage if on_hold is None else stage.on_hold(on_hold)


def drive(rig: Rig, stage: Stage[Any, Any, Any]) -> RunResult:
    """Runs the Stage inside the repository's workspace scope."""
    workflow = Workflow("t").context(WorkCtx).within(rig.checkout(), Flow("work").stage(stage))
    return rig.run(workflow)


def statuses(rig: Rig) -> list[str]:
    """The attempt record statuses in the order written."""
    return [record["status"] for record in rig.records("t") if record["kind"] == "attempt"]


def contract_event(rig: Rig) -> dict[str, Any]:
    """The attributes of the one contract event of the Stage."""
    (event,) = rig.spans.events("work", "factory.contract")
    return event


def agent_commits() -> AgentCommits[Any, Delivered]:
    return AgentCommits(subject=lambda i: PREFIX, reported=lambda event: event.commit)


def test_a_read_only_attempt_that_changes_nothing_passes(tmp_path: Path) -> None:
    """Given a read-only Stage, when the agent changes nothing, then the attempt passes."""
    rig = Rig(tmp_path).agent_does(idle())

    result = drive(rig, attempt(ReadOnly()))

    assert result.status == "completed" and contract_event(rig) == {"ok": True}


def test_a_read_only_attempt_that_moves_the_branch_head_fails_and_is_restored(tmp_path: Path) -> None:
    """Given a read-only Stage, when the agent commits, then the attempt fails and the branch is back at its start."""
    rig = Rig(tmp_path).agent_does(committing(f"{PREFIX}sneaky"))
    start = rig.repo.head()

    result = drive(rig, attempt(ReadOnly()))

    assert result.status == "failed" and "moved the branch head" in (result.reason or "")
    assert rig.repo.head() == start and rig.repo.status() == ""


def test_a_read_only_attempt_that_leaves_the_working_tree_changed_fails(tmp_path: Path) -> None:
    """Given a read-only Stage, when the agent leaves an edit, then the attempt fails and the tree is clean again."""
    rig = Rig(tmp_path).agent_does(editing())

    result = drive(rig, attempt(ReadOnly()))

    assert result.status == "failed" and "working tree" in (result.reason or "")
    assert rig.repo.status() == ""


def test_an_agent_commit_attempt_with_exactly_one_prefixed_commit_and_a_clean_tree_passes(tmp_path: Path) -> None:
    """Given an agent-commits Stage, when the agent makes one prefixed commit, then it passes on that commit."""
    rig = Rig(tmp_path).agent_does(committing(f"{PREFIX}add feature"))
    start = rig.repo.head()

    result = drive(rig, attempt(agent_commits()))

    assert result.status == "completed" and rig.repo.subjects()[:2] == [f"{PREFIX}add feature", "initial"]
    assert rig.repo.head() != start and statuses(rig)[-1] == "passed"


@pytest.mark.parametrize(
    ("action", "problem"),
    [
        (idle(), "found 0"),
        (lambda repo: committing(f"{PREFIX}one", "a.txt")(repo) and committing(f"{PREFIX}two", "b.txt")(repo), "found 2"),
        (committing("wrong subject"), "lacks the required prefix"),
        (lambda repo: committing(f"{PREFIX}one")(repo) and editing("dirty.txt")(repo), "not clean"),
    ],
    ids=["zero commits", "two commits", "wrong prefix", "dirty tree"],
)
def test_an_agent_commit_attempt_that_breaks_its_contract_fails_and_the_branch_is_restored(
    tmp_path: Path, action: Any, problem: str,
) -> None:
    """Given an agent-commits Stage, when the agent breaks the contract, then the attempt fails and the branch is restored."""
    rig = Rig(tmp_path).agent_does(action)
    start = rig.repo.head()

    result = drive(rig, attempt(agent_commits()))

    assert result.status == "failed" and problem in (result.reason or "")
    assert rig.repo.head() == start and rig.repo.status() == ""
    assert contract_event(rig)["ok"] is False


def test_an_agent_commit_attempt_whose_reported_commit_is_not_the_head_fails_and_is_restored(tmp_path: Path) -> None:
    """Given an agent-commits Stage, when the gate reports a commit that is not the head, then the attempt fails and is restored."""
    rig = Rig(tmp_path).agent_does(committing(f"{PREFIX}one"))
    start = rig.repo.head()

    result = drive(rig, attempt(agent_commits(), lambda i, reply, evidence: Pass(Delivered("0" * 40))))

    assert result.status == "failed" and "is not the branch head" in (result.reason or "")
    assert rig.repo.head() == start


def test_a_runtime_commit_attempt_whose_agent_moves_the_branch_head_fails_and_is_restored(tmp_path: Path) -> None:
    """Given a runtime-commits Stage, when the agent commits itself, then the attempt fails and the branch is restored."""
    rig = Rig(tmp_path).agent_does(committing("agent commit"))
    start = rig.repo.head()

    result = drive(rig, attempt(HarnessCommits(message=lambda i, event: "runtime")))

    assert result.status == "failed" and "moved the branch head" in (result.reason or "")
    assert rig.repo.head() == start


def test_a_runtime_commit_attempt_that_passes_verification_commits_once_on_top_of_its_start(tmp_path: Path) -> None:
    """Given a runtime-commits Stage, when verification passes and files changed, then exactly one declared commit is made."""
    rig = Rig(tmp_path).agent_does(editing())
    start = rig.repo.head()
    verified: list[bool] = []

    def verify(path: Path) -> None:
        verified.append((path / "work.txt").exists() and rig.repo.head() == start)

    result = drive(rig, attempt(HarnessCommits(message=lambda i, event: "runtime: declared", verify=verify)))

    assert result.status == "completed" and verified == [True]
    assert rig.repo.subjects() == ["runtime: declared", "initial"] and rig.repo.status() == ""
    assert statuses(rig) == ["running", "executed", "committing", "passed"]
    assert rig.spans.events("work", "factory.commit")[0]["sha"] == rig.repo.head()


def test_a_runtime_commit_attempt_with_nothing_changed_makes_no_commit(tmp_path: Path) -> None:
    """Given a runtime-commits Stage, when nothing changed, then the attempt passes and commits nothing."""
    rig = Rig(tmp_path).agent_does(idle())

    result = drive(rig, attempt(HarnessCommits(message=lambda i, event: "runtime")))

    assert result.status == "completed" and rig.repo.subjects() == ["initial"]
    assert rig.spans.events("work", "factory.commit") == []


def test_a_runtime_commit_attempt_whose_verification_fails_commits_nothing_and_fails(tmp_path: Path) -> None:
    """Given a runtime-commits Stage, when verification fails, then nothing is committed and the tree is restored."""
    rig = Rig(tmp_path).agent_does(editing())

    result = drive(rig, attempt(HarnessCommits(message=lambda i, event: "runtime", verify=lambda path: Fail("tests red"))))

    assert (result.status, result.reason) == ("failed", "tests red")
    assert rig.repo.subjects() == ["initial"] and rig.repo.status() == ""
    assert rig.spans.events("work", "factory.verify") == [{"ok": False}]


def test_a_failed_runtime_commit_attempt_with_a_restore_contract_leaves_the_start_commit_and_tree(tmp_path: Path) -> None:
    """Given a restore contract, when the gate fails an attempt that edited files, then branch and tree equal the start."""
    rig = Rig(tmp_path).agent_does(editing())
    start = rig.repo.head()

    result = drive(rig, attempt(HarnessCommits(message=lambda i, e: "m", on_fail=Restore()), lambda i, r, e: Fail("no")))

    assert result.status == "failed" and rig.repo.head() == start and rig.repo.status() == ""
    assert rig.spans.events("work", "factory.restore") == [{"to": start}]


def test_a_failed_runtime_commit_attempt_with_a_retain_contract_keeps_the_working_tree_and_is_not_retried(tmp_path: Path) -> None:
    """Given a retain contract, when the gate fails an attempt, then the edited tree stays and only one attempt starts."""
    rig = Rig(tmp_path).agent_does(editing())

    result = drive(rig, attempt(HarnessCommits(message=lambda i, e: "m", on_fail=Retain()), lambda i, r, e: Fail("no")))

    assert result.status == "failed" and rig.repo.status().strip() == "?? work.txt"
    assert rig.script.calls == 1 and len(rig.spans.attempts("work")) == 1
    assert rig.spans.events("work", "factory.restore") == []


@pytest.mark.parametrize(
    "contract", [ReadOnly(), agent_commits(), HarnessCommits(message=lambda i, e: "m"), HarnessCommits(message=lambda i, e: "m", on_fail=Retain())],
    ids=["read-only", "agent-commits", "runtime-restore", "runtime-retain"],
)
def test_a_held_attempt_leaves_no_repository_changes_and_with_no_hold_route_the_scope_stops_held(
    tmp_path: Path, contract: Any,
) -> None:
    """Given any repository contract, when the gate holds an attempt that edited files, then the repository is untouched and the scope is held."""
    rig = Rig(tmp_path).agent_does(editing())
    start = rig.repo.head()

    result = drive(rig, attempt(contract, lambda i, r, e: Hold("waiting")))

    assert (result.status, result.reason) == ("held", "waiting")
    assert rig.repo.head() == start and rig.repo.status() == ""
    assert statuses(rig) == ["running", "executed", "held"]


def test_a_declined_agent_reply_reaches_the_gate_as_a_value_and_the_gate_decides(tmp_path: Path) -> None:
    """Given an agent that declines, when the gate holds on the declined value, then the attempt is held, never passed."""
    rig = Rig(tmp_path).agent_does(declining("needs a person"))
    seen: list[Any] = []

    def gate(i: Any, reply: Any, evidence: Evidence) -> Any:
        seen.append(reply)
        return Hold(reply.reason) if isinstance(reply, Declined) else Pass(Delivered(evidence.head))

    result = drive(rig, attempt(NoRepository(), gate))

    assert seen == [Declined("needs a person")] and (result.status, result.reason) == ("held", "needs a person")
    assert "passed" not in statuses(rig)


def test_a_declined_agent_reply_with_no_gate_never_counts_as_a_pass(tmp_path: Path) -> None:
    """Given an agent that declines and no gate, when the Stage runs, then it fails with the agent's reason."""
    rig = Rig(tmp_path).agent_does(declining("cannot"))
    stage = Stage("work", NoInput, Delivered).agent(TESTER, lambda i, path: Ask("go"))

    result = drive(rig, stage)

    assert (result.status, result.reason) == ("failed", "cannot") and "passed" not in statuses(rig)


def test_an_agent_reply_is_stored_before_the_gate_reads_it(tmp_path: Path) -> None:
    """Given an agent Stage, when it runs, then the ledger keeps the executed reply with the attempt."""
    rig = Rig(tmp_path).agent_does(idle())

    drive(rig, attempt(NoRepository()))

    executed = next(record for record in rig.records("t") if record["kind"] == "attempt" and record["status"] == "executed")
    assert executed["reply"] == {"type": f"{Done.__module__}.Done", "fields": {"note": "done"}}

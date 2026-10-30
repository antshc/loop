"""Crash recovery: a run started again after any interruption converges to the uninterrupted outcome without repeating work."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from software_factory.runtime import (
    AgentCommits, Fail, Flow, HarnessCommits, Item, Ledger, Pass, Restore, Retain, Selector, Stage, WorkItemLog, Workflow,
    WorktreeGit, handoff, rework,
)

from .harness import TESTER, Ask, Done, Rig, WorkCtx, committing, command, declining, editing
from .support import Alpha, Beta, Delivered, NeedsDelivered, NeedsRouted, NeedsTicket, NoInput, Outcome, Published, Routed, Ticket

UNVERIFIED = "Unverified edits or unexpected commits retained in recovery worktree"
INTENDED = "ccode(t): work"


class Crash(Exception):
    """A process death nothing in the runtime catches."""


class CrashingLog(WorkItemLog):
    """A log that dies right after writing its `after`-th record."""

    def __init__(self, directory: Path, after: int) -> None:
        super().__init__(directory)
        self._after = after
        self._written = 0

    def append(self, record: Any) -> None:
        super().append(record)
        self._written += 1
        if self._written == self._after:
            raise Crash(f"died after record {self._written}")


class CrashingLedger(Ledger):
    """A ledger whose logs die after a given record."""

    def __init__(self, root: Path, after: int) -> None:
        super().__init__(root)
        self._after = after

    def item(self, workflow: str, item: str) -> WorkItemLog:
        return CrashingLog(super().item(workflow, item).path.parent, self._after)


@dataclass
class Remote:
    """The boundary an effect publishes to, which can be found done afterwards."""

    pushes: list[str] = field(default_factory=list)
    dies_after_pushing: bool = False

    def push(self) -> str:
        self.pushes.append("pr-1")
        if self.dies_after_pushing:
            raise Crash("died after pushing")
        return "pr-1"

    def find(self) -> str | None:
        return self.pushes[0] if self.pushes else None


def accepted(i: Any, reply: Any, evidence: Any) -> Pass[Delivered] | Fail[Any]:
    """The gate of the delivering Stage: a finished reply passes, a declined one fails."""
    return Pass(Delivered("work")) if isinstance(reply, Done) else Fail(reply.reason)


def delivery(rig: Rig, remote: Remote, *, retain: bool = False, then: Any = None) -> Workflow[WorkCtx]:
    """A pure check, then in the workspace an agent whose work the runtime commits, then an effect that publishes it."""
    check = Stage("check", NoInput, Routed).gate(lambda i, r, e: Pass(Routed("ok")))
    contract = HarnessCommits(lambda i, e: INTENDED, on_fail=Retain() if retain else Restore())
    implement = Stage("implement", NoInput, Delivered).agent(TESTER, lambda i, path: Ask("go")).contract(contract).gate(accepted)
    if not retain:
        implement = implement.on_fail(rework(max_attempts=3, then=then))
    publish = (
        Stage("publish", NeedsDelivered, Published).effect(lambda i: remote.push(), reconcile=lambda i: remote.find())
        .gate(lambda i, r, e: Pass(Published(r)))
    )
    if then is not None:
        publish = publish.on_fail(then)
    body = Flow("repo").stage(implement).stage(publish)
    return Workflow("t").context(WorkCtx).stage(check).within(rig.checkout(), body)


RECORDS = [
    "run-opened", "pure", "scope-opened", "implement:running", "implement:executed", "implement:committing",
    "implement:passed", "publish:running", "publish:executed", "publish:passed", "scope-closed", "run-closed",
]


def labels(records: Sequence[dict[str, Any]]) -> list[str]:
    """One label per record: its kind, or the Stage and status of an attempt."""
    return [f"{r['key']['item'].rsplit('/', 1)[1]}:{r['status']}" if r["kind"] == "attempt" else r["kind"] for r in records]


def outcome(rig: Rig) -> tuple[dict[str, str], str]:
    """The final status of each Stage's newest attempt and the kind and status of the last record."""
    newest: dict[str, tuple[int, str]] = {}
    records = rig.records("t")
    for record in records:
        if record["kind"] == "attempt":
            item = record["key"]["item"]
            if record["n"] >= newest.get(item, (0, ""))[0]:
                newest[item] = (record["n"], record["status"])
    return {item: status for item, (_, status) in newest.items()}, f"{records[-1]['kind']}:{records[-1].get('status')}"


def effects(rig: Rig, remote: Remote) -> tuple[int, list[str], list[str], str]:
    """What reached the boundaries: agent runs, publications, commits and the working tree."""
    return rig.script.calls, remote.pushes, rig.repo.subjects(), rig.repo.status()


def crash_after(rig: Rig, after: int, workflow: Workflow[WorkCtx], **options: Any) -> None:
    """Runs the Workflow and dies right after its `after`-th ledger record, then puts the ordinary ledger back."""
    root = rig.ledger.item("t", "item-1").path.parents[2]
    rig.ledger = CrashingLedger(root, after)
    with pytest.raises(Crash):
        rig.run(workflow, **options)
    rig.ledger = Ledger(root)


def test_the_uninterrupted_run_writes_the_records_the_crash_matrix_dies_after(tmp_path: Path) -> None:
    """Given the delivery Workflow, when it runs uninterrupted, then its ledger holds the records the matrix injects crashes after."""
    rig = Rig(tmp_path).agent_does(editing())

    result = rig.run(delivery(rig, Remote()))

    assert result.status == "completed" and labels(rig.records("t")) == RECORDS


@pytest.mark.parametrize("after", range(1, len(RECORDS)), ids=RECORDS[:-1])
def test_a_run_started_again_after_a_crash_reaches_the_uninterrupted_outcome(tmp_path: Path, after: int) -> None:
    """Given a crash right after any ledger record, when the run starts again, then it reaches the same final tail and the same boundary effects."""
    reference, ref_remote = Rig(tmp_path / "reference").agent_does(editing()), Remote()
    reference.run(delivery(reference, ref_remote))
    rig, remote = Rig(tmp_path / "crashed").agent_does(editing()), Remote()
    workflow = delivery(rig, remote)

    crash_after(rig, after, workflow)
    second = rig.run(workflow)

    assert second.status == "completed" and second.epoch == 1
    assert outcome(rig) == outcome(reference)
    assert effects(rig, remote) == effects(reference, ref_remote)


def test_an_effect_that_died_after_reaching_its_target_is_adopted_and_not_run_again(tmp_path: Path) -> None:
    """Given an effect that published and then died, when the run starts again, then its target shows it done and the reply is adopted without publishing twice."""
    rig, remote = Rig(tmp_path).agent_does(editing()), Remote(dies_after_pushing=True)
    workflow = delivery(rig, remote)
    with pytest.raises(Crash):
        rig.run(workflow)
    remote.dies_after_pushing = False

    result = rig.run(workflow)

    assert result.status == "completed" and remote.pushes == ["pr-1"]
    assert [label for label in labels(rig.records("t")) if label.startswith("publish")] == [
        "publish:running", "publish:executed", "publish:passed",
    ]


def test_a_commit_made_just_before_the_crash_is_adopted(tmp_path: Path) -> None:
    """Given the runtime died right after its commit, when the run starts again, then the commit is adopted: one commit, one agent run."""
    rig, remote = Rig(tmp_path).agent_does(editing()), Remote()
    workflow = delivery(rig, remote)

    def dies_after_commit(args: Sequence[str], path: Path, *, env: Any = None) -> str:
        output = command(args, path, env=env)
        if args[0] == "commit":
            raise Crash("died after committing")
        return output

    with pytest.raises(Crash):
        rig.run(workflow, git=WorktreeGit(dies_after_commit))
    result = rig.run(workflow)

    assert result.status == "completed"
    assert effects(rig, remote) == (1, ["pr-1"], [INTENDED, "initial"], "")
    assert [label for label in labels(rig.records("t")) if label.startswith("implement")] == [
        "implement:running", "implement:executed", "implement:committing", "implement:passed",
    ]


def test_an_interrupted_commit_with_the_wrong_subject_fails_as_an_unrecognized_commit(tmp_path: Path) -> None:
    """Given a crash during the runtime commit and someone else's commit on top of the base, when the run starts again, then nothing is pushed and the attempt fails as unrecognized."""
    rig, remote = Rig(tmp_path).agent_does(editing()), Remote()
    workflow = delivery(rig, remote)
    crash_after(rig, 6, workflow)
    rig.repo.commit("someone else's subject")

    result = rig.run(workflow)

    assert result.status == "failed" and result.reason is not None and result.reason.startswith("unrecognized commit")
    assert remote.pushes == [] and rig.repo.subjects() == ["someone else's subject", "initial"]


def test_a_commit_made_for_the_attempt_by_hand_with_the_intended_subject_is_adopted(tmp_path: Path) -> None:
    """Given a crash during the runtime commit after which the intended commit exists on the base, when the run starts again, then it is adopted and published."""
    rig, remote = Rig(tmp_path).agent_does(editing()), Remote()
    workflow = delivery(rig, remote)
    crash_after(rig, 6, workflow)
    rig.repo.commit(INTENDED)

    result = rig.run(workflow)

    assert result.status == "completed" and effects(rig, remote) == (1, ["pr-1"], [INTENDED, "initial"], "")


def test_after_the_reply_an_unchanged_working_tree_is_gated_again_without_asking_the_agent(tmp_path: Path) -> None:
    """Given a crash after the agent replied, when the working tree is unchanged, then the stored reply is gated and committed and the agent runs once."""
    rig, remote = Rig(tmp_path).agent_does(editing()), Remote()
    workflow = delivery(rig, remote)
    crash_after(rig, 5, workflow)

    result = rig.run(workflow)

    assert result.status == "completed" and effects(rig, remote) == (1, ["pr-1"], [INTENDED, "initial"], "")


def test_after_the_reply_a_changed_working_tree_fails_as_unverified_and_stages_nothing(tmp_path: Path) -> None:
    """Given a crash after the agent replied, when a stray file appeared, then the attempt fails as unverified and nothing is staged, committed or pushed."""
    rig, remote = Rig(tmp_path).agent_does(editing()), Remote()
    workflow = delivery(rig, remote)
    crash_after(rig, 5, workflow)
    rig.repo.write("stray.txt", "x\n")

    result = rig.run(workflow)

    assert (result.status, result.reason) == ("failed", UNVERIFIED)
    assert rig.repo.status().splitlines() == ["?? stray.txt", "?? work.txt"]
    assert effects(rig, remote)[:3] == (1, [], ["initial"])


def test_before_the_reply_the_interrupted_attempt_counts_and_the_work_is_restored(tmp_path: Path) -> None:
    """Given a crash after the agent started and left edits, when the run starts again, then the tree is restored, the attempt counts and the rework runs the agent anew."""
    rig, remote = Rig(tmp_path).agent_does(editing()), Remote()
    workflow = delivery(rig, remote)
    crash_after(rig, 4, workflow)
    rig.repo.write("half.txt", "partial\n")

    result = rig.run(workflow)

    attempts = [(r["n"], r["status"]) for r in rig.records("t") if r["kind"] == "attempt" and r["key"]["item"].endswith("implement")]
    assert result.status == "completed" and attempts[:3] == [(1, "running"), (1, "failed"), (2, "running")]
    assert rig.repo.subjects() == [INTENDED, "initial"] and rig.repo.status() == ""


def test_a_retained_attempt_interrupted_before_the_reply_reruns_only_on_a_clean_tree_at_its_base(tmp_path: Path) -> None:
    """Given a retaining Stage that was interrupted before the reply, when the tree is clean at the base, then it runs again and counts; when it is dirty it fails as unverified."""
    clean, dirty = Rig(tmp_path / "clean").agent_does(editing()), Rig(tmp_path / "dirty").agent_does(editing())
    clean_remote, dirty_remote = Remote(), Remote()
    for rig, remote in ((clean, clean_remote), (dirty, dirty_remote)):
        crash_after(rig, 4, delivery(rig, remote, retain=True))
    dirty.repo.write("stray.txt", "x\n")

    rerun = clean.run(delivery(clean, clean_remote, retain=True))
    refused = dirty.run(delivery(dirty, dirty_remote, retain=True))

    assert rerun.status == "completed" and clean.script.calls == 1 and clean_remote.pushes == ["pr-1"]
    assert (refused.status, refused.reason) == ("failed", UNVERIFIED) and dirty.script.calls == 0


def test_a_retained_attempt_interrupted_during_the_commit_is_held_for_inspection(tmp_path: Path) -> None:
    """Given a retaining Stage that died before its commit landed, when the run starts again, then it holds for inspection and neither commits nor publishes."""
    rig, remote = Rig(tmp_path).agent_does(editing()), Remote()
    crash_after(rig, 6, delivery(rig, remote, retain=True))

    result = rig.run(delivery(rig, remote, retain=True))

    assert (result.status, result.reason) == ("failed", UNVERIFIED)
    assert effects(rig, remote) == (1, [], ["initial"], "?? work.txt\n")


def test_a_stray_file_in_a_resumed_workspace_scope_is_never_committed_or_pushed(tmp_path: Path) -> None:
    """Given a crash after the work was accepted, when a stray file appears, then nothing is committed or pushed and the scope ends with the recovery reason."""
    rig, remote = Rig(tmp_path).agent_does(editing()), Remote()
    workflow = delivery(rig, remote)
    crash_after(rig, 7, workflow)
    rig.repo.write("stray.txt", "x\n")

    result = rig.run(workflow)

    assert (result.status, result.reason) == ("failed", UNVERIFIED)
    assert effects(rig, remote) == (1, [], [INTENDED, "initial"], "?? stray.txt\n")


def test_someone_elses_commit_in_a_resumed_workspace_scope_is_never_pushed(tmp_path: Path) -> None:
    """Given a crash after the work was accepted, when someone else commits, then it is never pushed and the scope ends with the recovery reason."""
    rig, remote = Rig(tmp_path).agent_does(editing()), Remote()
    workflow = delivery(rig, remote)
    crash_after(rig, 7, workflow)
    rig.repo.write("other.txt", "theirs\n")
    rig.repo.commit("someone else")

    result = rig.run(workflow)

    assert (result.status, result.reason) == ("failed", UNVERIFIED)
    assert remote.pushes == [] and rig.script.calls == 1


def test_a_hand_off_stage_lands_even_when_the_resume_check_fails(tmp_path: Path) -> None:
    """Given a resumed scope whose check fails and a hand-off route, when the run starts again, then the hand-off effect runs and the run is handed off."""
    rig, remote, labelled = Rig(tmp_path).agent_does(editing()), Remote(), []
    block = Stage("block", NoInput, Routed).effect(lambda i: labelled.append("hitl"), reconcile=lambda i: None).gate(lambda i, r, e: Pass(Routed("b")))
    workflow = delivery(rig, remote, then=handoff(block))
    crash_after(rig, 7, workflow)
    rig.repo.write("stray.txt", "x\n")

    result = rig.run(workflow)

    assert (result.status, labelled, remote.pushes) == ("handed_off", ["hitl"], [])
    assert result.reason == UNVERIFIED


def test_a_handed_off_scope_stays_open_until_a_release_then_starts_a_fresh_epoch_and_rework_budget(tmp_path: Path) -> None:
    """Given a scope handed off after its rework budget ran out, when later runs start, then each holds with the same reason and runs no agent until a release gives a fresh epoch and budget."""
    rig, remote, labelled = Rig(tmp_path).agent_does(declining()), Remote(), []
    block = Stage("block", NoInput, Routed).effect(lambda i: labelled.append("hitl") or "fp", reconcile=lambda i: None).gate(lambda i, r, e: Pass(Routed(r)))
    workflow = delivery(rig, remote, then=handoff(block))

    first = rig.run(workflow)
    second, third = rig.run(workflow), rig.run(workflow)
    held = rig.ledger.status("t", "item-1")
    rig.ledger.release("t", "item-1", "a person looked")
    released = rig.ledger.status("t", "item-1")
    fourth = rig.run(workflow)

    assert [(r.status, r.epoch, r.reason) for r in (first, second, third)] == [("handed_off", 1, "cannot")] * 3
    assert held is not None and (held.status, held.released) == ("handed_off", False)
    assert released is not None and released.released
    assert (fourth.status, fourth.epoch) == ("handed_off", 2)
    assert rig.script.calls == 6 and labelled == ["hitl", "hitl"] and remote.pushes == []
    assert [r["status"] for r in rig.records("t") if r["kind"] == "scope-closed"] == ["released"]
    assert [r["kind"] for r in rig.records("t")].count("released") == 1


def test_a_run_resumes_the_unfinished_item_with_its_persisted_value_without_selecting_again(tmp_path: Path) -> None:
    """Given a loop whose second item crashed, when the run starts again and the Selector now offers another item, then the unfinished item resumes first and the Selector's answer applies after it."""
    rig, ran, offers = Rig(tmp_path), [], {"run": 1, "crash": True}

    def work(i: Any) -> None:
        ran.append(i.ticket.number)
        if i.ticket.number == 2 and offers["crash"]:
            offers["crash"] = False
            raise Crash("died")

    def select(_: NoInput) -> list[Item[Ticket]]:
        numbers = [1, 2] if offers["run"] == 1 else [3]
        return [Item(Ticket(n), str(n), f"r{n}") for n in numbers if n not in ran or n == 2 and offers["crash"]]

    stage = Stage("work", NeedsTicket, Alpha).effect(work, reconcile=lambda i: None).gate(lambda i, r, e: Pass(Alpha()))
    workflow = Workflow("t").context(WorkCtx).each(Selector("tickets", NoInput, Ticket, select), Flow("ticket").stage(stage))
    with pytest.raises(Crash):
        rig.run(workflow)
    offers["run"] = 2

    result = rig.run(workflow)

    revisions = {r["key"]["revision"] for r in rig.records("t") if r["kind"] == "attempt" and r["key"]["item"].endswith("ticket/2/work")}
    assert result.status == "completed" and ran == [1, 2, 2, 3] and revisions == {"r2"}


def test_a_run_resumes_the_unfinished_switch_case_without_deciding_again(tmp_path: Path) -> None:
    """Given a run that crashed inside the Alpha case, when it starts again, then the Alpha case resumes and the deciding check replays its recorded verdict."""
    rig, log, decided = Rig(tmp_path), [], []

    def decide(_: NoInput) -> None:
        decided.append(len(decided) + 1)

    def alpha(_: NoInput) -> None:
        log.append("alpha")
        if len(log) == 1:
            raise Crash("died")

    deciding = Stage("decide", NoInput, Outcome).action(decide).gate(lambda i, r, e: Pass(Alpha() if len(decided) == 1 else Beta()))
    on_alpha = Stage("on-alpha", NoInput, Alpha).effect(alpha, reconcile=lambda i: None).gate(lambda i, r, e: Pass(Alpha()))
    on_beta = Stage("on-beta", NoInput, Beta).effect(lambda i: log.append("beta"), reconcile=lambda i: None).gate(lambda i, r, e: Pass(Beta()))
    workflow = Workflow("t").context(WorkCtx).stage(deciding).dispatch(Outcome, {Alpha: Flow("a").stage(on_alpha), Beta: Flow("b").stage(on_beta)})
    with pytest.raises(Crash):
        rig.run(workflow)

    result = rig.run(workflow)

    assert result.status == "completed" and log == ["alpha", "alpha"] and decided == [1]


@pytest.mark.parametrize(("revision", "first_runs", "read"), [("r1", 1, "call-1"), ("r2", 2, "call-2")])
def test_a_finished_stage_replays_for_the_same_revision_and_runs_again_for_a_new_one(
    tmp_path: Path, revision: str, first_runs: int, read: str,
) -> None:
    """Given a crash after the first Stage passed, when the run starts again, then the Stage replays its Event under the same input revision and runs again under a new one."""
    rig, calls, seen, crashes = Rig(tmp_path), [], [], {"left": 1}
    first = Stage("first", NoInput, Routed).effect(lambda i: calls.append("x") or f"call-{len(calls)}", reconcile=lambda i: None).gate(lambda i, r, e: Pass(Routed(r)))

    def reading(i: Any) -> None:
        seen.append(i.routed.target)
        if crashes["left"]:
            crashes["left"] -= 1
            raise Crash("died")

    second = Stage("second", NeedsRouted, Routed).effect(reading, reconcile=lambda i: None).gate(lambda i, r, e: Pass(Routed("done")))
    workflow = Workflow("t").context(WorkCtx).stage(first).stage(second)
    with pytest.raises(Crash):
        rig.run(workflow)

    rig.run(workflow, context=WorkCtx(revision=revision))

    assert (len(calls), seen) == (first_runs, ["call-1", read])


def test_a_replayed_accepted_commit_that_left_the_branch_becomes_a_failure(tmp_path: Path) -> None:
    """Given a passed attempt whose commit was later dropped from the branch, when the run starts again, then the replayed outcome is a failure and the agent is not asked."""
    rig = Rig(tmp_path).agent_does(committing(f"{INTENDED}"))
    contract = AgentCommits(subject=lambda i: INTENDED, reported=lambda event: event.commit)
    work = Stage("work", NoInput, Delivered).agent(TESTER, lambda i, path: Ask("go")).contract(contract).gate(lambda i, r, e: Pass(Delivered(e.head)))
    late = Stage("late", NoInput, Routed).effect(lambda i: (_ for _ in ()).throw(Crash("died")), reconcile=lambda i: None).gate(lambda i, r, e: Pass(Routed("x")))
    workflow = Workflow("t").context(WorkCtx).within(rig.checkout(), Flow("repo").stage(work).stage(late))
    with pytest.raises(Crash):
        rig.run(workflow)
    command(("reset", "--hard", "HEAD~1"), rig.repo.path)

    result = rig.run(workflow)

    assert (result.status, result.reason) == ("failed", "the accepted commit is no longer on the branch")
    assert rig.script.calls == 1


@pytest.mark.parametrize(("revision", "evaluated", "verdict"), [("r1", [1], "seen-1"), ("r2", [1, 2], "seen-2")])
def test_recorded_outcomes_of_side_effect_free_checks_are_replayed_with_their_verdicts_for_the_same_revision(
    tmp_path: Path, revision: str, evaluated: list[int], verdict: str,
) -> None:
    """Given a check that was recorded before the run crashed, when the run starts again, then its verdict is replayed on the same input revision and evaluated afresh on a new one."""
    rig, runs, crashes = Rig(tmp_path), [], {"left": 1}

    def check(_: NoInput) -> None:
        runs.append(len(runs) + 1)

    def publish(i: NeedsRouted) -> str:
        if crashes["left"]:
            crashes["left"] -= 1
            raise Crash("died")
        return i.routed.target

    checking = Stage("check", NoInput, Routed).action(check).gate(lambda i, r, e: Pass(Routed(f"seen-{len(runs)}")))
    publishing = Stage("publish", NeedsRouted, Routed).effect(publish, reconcile=lambda i: None).gate(lambda i, r, e: Pass(Routed(r)))
    workflow = Workflow("t").context(WorkCtx).stage(checking).stage(publishing)
    with pytest.raises(Crash):
        rig.run(workflow)

    result = rig.run(workflow, context=WorkCtx(revision=revision))

    assert result.status == "completed" and runs == evaluated
    assert rig.ledger.item("t", "item-1").history(Routed)[-1] == Routed(verdict)


def test_a_once_per_key_stage_records_its_replay_in_the_current_epoch_without_repeating_its_event(tmp_path: Path) -> None:
    """Given a once-per-key Stage that passed in an earlier run, when a later run replays it, then a replayed record is written in that epoch and History holds the Event once."""
    rig, calls = Rig(tmp_path), []
    once = Stage("push", NoInput, Routed).effect(lambda i: calls.append("x") or "r", reconcile=lambda i: None).gate(lambda i, r, e: Pass(Routed(r))).once(per=lambda _: "k")
    workflow = Workflow("t").context(WorkCtx).stage(once)

    rig.run(workflow)
    rig.run(workflow)

    replays = [r for r in rig.records("t") if r["kind"] == "replayed"]
    assert len(calls) == 1 and len(replays) == 1 and replays[0]["key"]["epoch"] == 2
    assert rig.ledger.item("t", "item-1").history(Routed) == (Routed("r"),)


def test_a_limited_stage_that_passed_replays_after_a_crash_in_the_same_epoch_instead_of_failing(tmp_path: Path) -> None:
    """Given a Stage limited to one attempt that passed before a crash, when the run starts again, then it replays its pass rather than reporting an exhausted budget."""
    rig, calls, crashes = Rig(tmp_path), [], {"left": 1}
    limited = Stage("push", NoInput, Routed).effect(lambda i: calls.append("x") or "r", reconcile=lambda i: None).gate(lambda i, r, e: Pass(Routed(r))).limit(1, per=lambda _: "k")

    def later(_: NoInput) -> None:
        if crashes["left"]:
            crashes["left"] -= 1
            raise Crash("died")

    last = Stage("later", NoInput, Alpha).effect(later, reconcile=lambda i: None).gate(lambda i, r, e: Pass(Alpha()))
    workflow = Workflow("t").context(WorkCtx).stage(limited).stage(last)
    with pytest.raises(Crash):
        rig.run(workflow)

    result = rig.run(workflow)

    assert result.status == "completed" and calls == ["x"]


def test_a_scope_that_writes_nothing_durable_has_no_record_in_the_ledger(tmp_path: Path) -> None:
    """Given a workspace scope holding only a check, when the run ends, then the ledger holds the run records alone."""
    rig = Rig(tmp_path)
    check = Stage("check", NoInput, Routed).gate(lambda i, r, e: Pass(Routed("ok")))

    rig.run(Workflow("t").context(WorkCtx).within(rig.checkout(), Flow("repo").stage(check)))

    assert labels(rig.records("t")) == ["run-opened", "run-closed"]


def test_a_torn_last_line_is_ignored_and_the_next_record_starts_on_its_own_line(tmp_path: Path) -> None:
    """Given a crash that left half a record on the last line, when the run starts again, then the fragment is ignored and the run completes with a readable log."""
    rig, remote = Rig(tmp_path).agent_does(editing()), Remote()
    workflow = delivery(rig, remote)
    crash_after(rig, 4, workflow)
    log = rig.ledger.item("t", "item-1").path
    log.write_text(log.read_text() + '{"v":1,"kind":"attempt","key":{"item":"t/ite')

    result = rig.run(workflow)

    assert result.status == "completed" and labels(rig.records("t"))[-1] == "run-closed"
    assert remote.pushes == ["pr-1"]

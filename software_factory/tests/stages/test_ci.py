"""The CI Stages: the diagnosis the maintainer is asked once per head and CI-run signal, the capped rerun of failed jobs, and the verified fix that is committed and published."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from loop import WorktreeError

from software_factory.agents.roles.maintainer import DIAGNOSE_CI, FIX_CI, Diagnosis, FixRequest
from software_factory.events.ci import BranchBroken, CiFixed, CiRerun, Transient
from software_factory.events.feedback import IssueResolved, IssueUnclear
from software_factory.events.pull_requests import PrBlocked, PullRequestPolled, Unsupported
from software_factory.events.specs import RepositoryTarget
from software_factory.runtime import Evidence, Fail, Flow, Hold, Pass, Workflow, handoff
from software_factory.shared.github.repo import CommandError
from software_factory.stages import ci, pull_requests, workspaces
from tests.runtime.harness import command
from tests.workflows.pr_babysit.golden import CLOSED, GOLDEN, GREEN, OPEN, build_host, pulls_over
from tests.shared.fakes import check_run
from tests.stages.pr_support import BRANCH, Verifier, polled_of
from tests.stages.watch_support import MaintainerAgent, WatchRig, fingerprint_of, produces, watch

EVIDENCE = Evidence("", "", False)
SUBJECT = "babysit: fix CI (PR #7)"


def maintain(rig: WatchRig, *, verify: Verifier | None = None, attempts: int = 2) -> Workflow[Any]:
    """Snapshot, then the CI activity of the babysit definition inside the pull request worktree, every failure handed off to the block."""
    block = handoff(pull_requests.block_pr(fingerprint=fingerprint_of))
    rerun = Flow("rerun").stage(ci.rerun_failed().limit(attempts, per=ci.head).on_fail(block))
    fixing = (
        Flow("fix")
        .stage(ci.fix(message=lambda i: f"babysit: fix CI (PR #{i.polled.number})", verify=verify or Verifier()).on_fail(block))
        .stage(pull_requests.publish_batch().on_fail(block))
    )
    activity = (
        Flow("ci")
        .stage(workspaces.require_clean().on_hold(block))
        .stage(ci.diagnose().on_hold(block))
        .dispatch(ci.Diagnosis, {ci.Transient: rerun, ci.BranchBroken: fixing})
    )
    return (
        watch(pull_requests.snapshot())
        .within(workspaces.pr_worktree(), activity)
        .stage(produces(IssueResolved("b", "i", "fix-needed", ())))
        .stage(produces(IssueUnclear("b", "i", "why", ())))
        .stage(produces(Unsupported("x")))
    )


def replays(rig: WatchRig) -> list[dict]:
    """The attributes of every replay the runs recorded, in order."""
    return [event.attributes for root in rig.spans.roots for event in _events(root) if event.name == "factory.replay"]


def _events(span):
    yield from span.events
    for child in span.children:
        yield from _events(child)


# ---- diagnose ------------------------------------------------------------------------------------------------


def test_diagnose_replays_the_recorded_diagnosis_for_the_same_head_and_ci_run_signal(tmp_path: Path) -> None:
    """Given a transient diagnosis already recorded for the head and its failed runs, when the same state is diagnosed again, then the recorded diagnosis is replayed and the maintainer is not asked."""
    rig = WatchRig(tmp_path)
    rig.fail_ci()
    agent = MaintainerAgent("transient")

    first, second = rig.run_watch(maintain(rig), agent), rig.run_watch(maintain(rig), agent)

    assert (first.status, second.status) == ("completed", "completed")
    assert agent.modes() == ["diagnose"]
    assert replays(rig) == [{"stage": "diagnose-ci", "outcome": "pass"}]


def test_diagnose_asks_the_maintainer_again_when_the_ci_run_signal_changes(tmp_path: Path) -> None:
    """Given a diagnosis for a failed run, when the same run fails again as a later attempt, then the maintainer is asked again."""
    rig = WatchRig(tmp_path)
    agent = MaintainerAgent("transient")

    rig.fail_ci(attempt=1)
    rig.run_watch(maintain(rig), agent)
    rig.fail_ci(attempt=2)
    rig.run_watch(maintain(rig), agent)

    assert agent.modes() == ["diagnose", "diagnose"]


def test_diagnose_sends_the_maintainer_the_failed_run_logs_or_says_there_are_none(tmp_path: Path) -> None:
    """Given failed workflow runs, then the request carries their logs; given a failure without a workflow run, then it says no logs exist."""
    rig = WatchRig(tmp_path)
    agent = MaintainerAgent("transient")
    rig.fail_ci()
    rig.run_watch(maintain(rig), agent)
    rig.pull.workflow_runs = []
    rig.pull.check_runs = lambda: [check_run(1, "test", conclusion="failure", commit=rig.head_now())]

    rig.run_watch(maintain(rig), agent)

    assert [request["logs"] for request in agent.requests] == [rig.host.logs, "No Actions run logs; inspect check/status links."]


@pytest.mark.parametrize(("reason", "reported"), [("flaky infra and a real bug", "flaky infra and a real bug"), ("", "CI diagnosis is unclear")])
def test_an_unclear_or_blocked_diagnosis_holds_and_is_handed_to_the_block(tmp_path: Path, reason: str, reported: str) -> None:
    """Given a maintainer that cannot classify the failure, when diagnosed, then the run is handed off with its reason (or that the diagnosis is unclear) and nothing is rerun or fixed."""
    rig = WatchRig(tmp_path)
    rig.fail_ci()
    agent = MaintainerAgent("blocked", reason)

    result = rig.run_watch(maintain(rig), agent)

    assert result.status == "handed_off" and result.reason == reported
    assert rig.closed_run_attributes()["factory.pr.reason"] == reported
    assert rig.host.retries == [] and agent.modes() == ["diagnose"]


def test_the_diagnose_stage_classifies_transient_and_branch_failures() -> None:
    """Given the maintainer's classifications, when gated, then transient and branch pass as their Events and anything else holds."""
    stage = ci.diagnose()

    verdicts = {kind: stage.judge(None, Diagnosis(kind, "why"), EVIDENCE) for kind in ("transient", "branch", "blocked")}

    assert verdicts == {"transient": Pass(Transient("why")), "branch": Pass(BranchBroken("why")), "blocked": Hold("why")}


# ---- rerun_failed --------------------------------------------------------------------------------------------


def test_a_transient_failure_reruns_every_failed_run_once_per_failure(tmp_path: Path) -> None:
    """Given a transient failure of two workflow runs, when maintained twice, then each failed run is rerun once and the second pass reruns nothing."""
    rig = WatchRig(tmp_path)
    rig.fail_ci(runs=(10, 11))
    agent = MaintainerAgent("transient")

    rig.run_watch(maintain(rig), agent)
    rig.run_watch(maintain(rig), agent)

    assert rig.host.retries == [11, 10]
    assert [event.runs for event in rig.recorded(CiRerun)] == [(11, 10)]


def test_a_transient_failure_is_not_rerun_while_ci_is_still_pending(tmp_path: Path) -> None:
    """Given a transient failure while other CI is still running, when maintained, then nothing is rerun and the run completes."""
    rig = WatchRig(tmp_path)
    rig.fail_ci()
    rig.pend_ci()

    result = rig.run_watch(maintain(rig), MaintainerAgent("transient"))

    assert result.status == "completed" and rig.host.retries == []


def test_a_transient_failure_with_no_failed_workflow_run_is_handed_to_the_block(tmp_path: Path) -> None:
    """Given a failed check that no workflow run explains, when it is diagnosed transient, then there is nothing to rerun and the run is handed off."""
    rig = WatchRig(tmp_path)
    rig.pull.check_runs = lambda: [check_run(1, "test", conclusion="failure", commit=rig.head_now())]

    result = rig.run_watch(maintain(rig), MaintainerAgent("transient"))

    assert result.status == "handed_off" and result.reason == "No failed workflow run to rerun"
    assert rig.host.retries == []


def test_rerun_failed_stops_at_the_started_attempt_cap_per_head_even_after_an_interruption(tmp_path: Path) -> None:
    """Given a cap of two attempts per head and a first attempt that crashed the run, when the failure recurs, then one more rerun happens and a further attempt fails without rerunning CI."""
    rig = WatchRig(tmp_path)
    agent = MaintainerAgent("transient")
    rig.gh.fail_on("run", "rerun")

    rig.fail_ci(attempt=1)
    with pytest.raises(CommandError):
        rig.run_watch(maintain(rig), agent)
    rig.fail_ci(attempt=2)
    second = rig.run_watch(maintain(rig), agent)
    rig.fail_ci(attempt=3)
    third = rig.run_watch(maintain(rig), agent)

    assert (second.status, third.status) == ("completed", "handed_off")
    assert third.reason == f"Stage 'rerun-failed' has already started 2 of its 2 attempts for '{rig.head}'"
    assert rig.host.retries == [10]


# ---- fix -----------------------------------------------------------------------------------------------------


def test_a_branch_failure_is_fixed_verified_committed_once_with_the_subject_and_published(tmp_path: Path) -> None:
    """Given a branch failure the maintainer fixes, when maintained, then the fix is verified in the worktree, committed once with the given subject and pushed, and nobody is replied to."""
    rig = WatchRig(tmp_path)
    rig.fail_ci()
    verifier = Verifier()

    result = rig.run_watch(maintain(rig, verify=verifier), MaintainerAgent("branch"))

    assert result.status == "completed"
    assert rig.subjects() == [SUBJECT] and verifier.paths == [rig.worktree()]
    assert rig.remote_head(BRANCH) == command_head(rig) and rig.remote_head(BRANCH) != rig.head
    assert rig.host.replies == []


def test_a_fix_that_fails_verification_commits_nothing_and_keeps_the_worktree(tmp_path: Path) -> None:
    """Given a fix that fails verification, when maintained, then the block hand-off runs, nothing is committed or pushed and the edit stays in the retained worktree Loop preserves."""
    rig = WatchRig(tmp_path)
    rig.fail_ci()

    with pytest.raises(WorktreeError, match="preserved"):
        rig.run_watch(maintain(rig, verify=Verifier(fail=lambda _: True)), MaintainerAgent("branch"))

    assert len(rig.recorded(PrBlocked)) == 1
    assert rig.subjects() == [] and rig.remote_head(BRANCH) == rig.head
    assert (rig.worktree() / "fix.txt").read_text() == "fixed\n"


@pytest.mark.parametrize(
    ("fix", "reason"),
    [
        (lambda cwd: {"verdict": "blocked", "reason": "needs a secret"}, "needs a secret"),
        (lambda cwd: {"verdict": "blocked", "reason": ""}, "Maintainer could not fix CI"),
        (lambda cwd: {"verdict": "fixed", "reason": "done"}, "Maintainer claimed a fix but produced no changes"),
    ],
    ids=["blocked with a reason", "blocked without one", "fixed without a change"],
)
def test_a_fix_the_maintainer_cannot_make_or_did_not_make_fails_and_is_handed_off(tmp_path: Path, fix, reason: str) -> None:
    """Given a maintainer that is blocked or claims a fix without changing anything, when maintained, then nothing is committed and the run is handed off with the reason."""
    rig = WatchRig(tmp_path)
    rig.fail_ci()

    result = rig.run_watch(maintain(rig), MaintainerAgent("branch", fix=fix))

    assert (result.status, result.reason) == ("handed_off", reason)
    assert rig.subjects() == [] and rig.recorded(PrBlocked)


def test_the_fix_stage_passes_a_verified_change_as_the_ci_fix_event_of_the_head() -> None:
    """Given a maintainer that reports a fix with a changed worktree, when gated, then the CI fix Event names the head the fix was made on."""
    polled = PullRequestPolled(None, 7, {"pr": {"head": {"sha": "abc", "ref": "f"}}})
    work = ci.FixInput(polled, BranchBroken("why"))

    verdict = ci.fix(message=lambda _: "m", verify=lambda _: None).judge(work, ci.Fixed("fixed", "repaired"), Evidence("a", "b", True))

    assert verdict == Pass(CiFixed("abc", "repaired"))
    assert ci.fix(message=lambda _: "m", verify=lambda _: None).judge(work, ci.Fixed("fixed", ""), EVIDENCE) == Fail("Maintainer claimed a fix but produced no changes")


# ---- envelopes -----------------------------------------------------------------------------------------------


class RecordedLogs:
    """Stands in for the GitHub account with the logs babysit's golden diagnose envelope carries; remembers the runs asked about."""

    def __init__(self) -> None:
        self.runs: list[int] = []

    def pull_requests(self, slug: str) -> RecordedLogs:
        return self

    def failed_logs(self, run_ids) -> str:
        self.runs = list(run_ids)
        return "cart.py: duplicate product"


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text())


def golden_polled(number: int):
    pulls = pulls_over(build_host())
    return polled_of(RepositoryTarget("a/b", Path("/work"), Path("/trees")), pulls.snapshot(number), "me")


def test_the_diagnose_request_is_byte_identical_to_babysits_envelope_golden_and_asks_for_the_failed_runs_logs(golden) -> None:
    """Given the golden pull request with failed CI, when the diagnose request is built, then its rendering equals the envelope captured before the migration and the logs of the failed runs, latest first, were read."""
    logs = RecordedLogs()
    request = ci.diagnose().executor.request(ci.DiagnoseInput(golden_polled(OPEN), logs), Path("/work"))

    assert DIAGNOSE_CI.encode(request) == golden["open"]["envelopes"]["diagnose"]
    assert logs.runs == [11, 10]


@pytest.mark.parametrize("scenario,number", [("open", OPEN), ("closed", CLOSED), ("green", GREEN)])
def test_the_fix_request_is_byte_identical_to_babysits_envelope_golden(golden, scenario: str, number: int) -> None:
    """Given each golden pull request, when the fix request is built from a branch diagnosis, then its rendering equals the envelope captured before the migration."""
    work = ci.FixInput(golden_polled(number), BranchBroken("CI evidence"))

    request = ci.fix(message=lambda _: "m", verify=lambda _: None).executor.request(work, Path("/work"))

    assert request == FixRequest(Diagnosis("branch", "CI evidence"), work.polled.snapshot)
    assert FIX_CI.encode(request) == golden[scenario]["envelopes"]["fix"]


def command_head(rig: WatchRig) -> str:
    """The head of the pull request's recovery branch."""
    return command(("rev-parse", rig.recovery_branch()), rig.repo.path).strip()

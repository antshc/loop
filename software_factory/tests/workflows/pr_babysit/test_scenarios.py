"""pr_babysit scenarios through the command line: what each poll does, how a run ends and what stays behind, over fake GitHub, a real repository and scripted agents."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from loop import AgentRequest, FakeReply, RunContext

from software_factory.events.feedback import FeedbackHandled
from software_factory.events.pull_requests import CopilotRequested, ReadinessReported
from tests.runtime.harness import StopAfter, command
from tests.stages.pr_support import answer, reply_text, thread
from factory_workflows.pr_babysit import __main__ as entry
from factory_workflows.pr_babysit.policy import FINGERPRINT, REASON, fingerprint

from .harness import FAILS_AFTER_EDIT, FAILS_UNTIL_FIXED, Babysit, LocalOrigin

KEY = "thread:t1:t1c"


def review() -> list[dict]:
    """One open review thread; a new one each call, because hosting a reply changes it."""
    return [thread("t1", "Handle the empty cart")]


def addressing(tmp_path: Path, **options) -> Babysit:
    """A pull request with one review thread whose fix edits `cart.py`."""
    rig = Babysit(tmp_path, threads=review(), **options)
    rig.agent.address.handlers["i1"] = answer("fix-needed", edit="cart.py")
    return rig


class Garbage:
    """An agent whose reply is no JSON."""

    def __call__(self, request: AgentRequest, context: RunContext) -> FakeReply:
        return FakeReply("not json")


class RejectPush(LocalOrigin):
    """Git whose push is refused."""

    def publish(self, path: Path, branch: str) -> None:
        raise subprocess.CalledProcessError(1, ["git", "push"])


def test_feedback_is_addressed_verified_committed_pushed_and_replied_to_once_and_the_run_completes(tmp_path: Path) -> None:
    """Given a review thread, when the pull request is babysat until it closes, then the issue is fixed in a commit that is pushed, the reply is posted once and the run completes with exit 0."""
    rig = addressing(tmp_path)

    code = rig.babysit()

    assert code == 0 and rig.status().status == "completed"
    assert rig.agent.modes == ["group", "resolve"]
    assert rig.replies() == [reply_text(KEY)]
    assert rig.commits() == ["babysit: address i1 (PR #7)"] and rig.pushed()
    assert rig.remote_head("feature") == command(("rev-parse", rig.recovery_branch()), rig.repo.path).strip()
    assert rig.history(FeedbackHandled)[0].keys == (KEY,)
    assert not rig.worktree().exists()


def test_a_closed_pull_request_ends_the_run_completed_without_any_agent(tmp_path: Path) -> None:
    """Given a merged pull request, when it is babysat, then the run ends completed, exits 0 and no agent ran."""
    rig = addressing(tmp_path)
    rig.pull.close(merged=True)

    code = rig.babysit()

    assert code == 0 and rig.status().status == "completed" and rig.agent.modes == []
    assert rig.replies() == [] and not rig.pushed()


def test_a_failed_ci_is_diagnosed_fixed_and_published_by_the_maintainer(tmp_path: Path) -> None:
    """Given failing CI and no feedback, when the pull request is babysat, then exactly CI maintenance runs: diagnose, fix, a verified commit and a push."""
    rig = Babysit(tmp_path)
    rig.fail_ci_until_fixed()

    code = rig.babysit()

    assert code == 0
    assert rig.agent.modes == ["diagnose", "fix"]
    assert rig.commits() == ["babysit: fix CI (PR #7)"] and rig.pushed() and rig.replies() == []


def test_a_transient_failure_is_rerun_once_per_failure_and_a_repeated_snapshot_spends_nothing_more(tmp_path: Path) -> None:
    """Given a transient CI failure that stays red for three polls, when the pull request is babysat, then the failed run is rerun once and the agent diagnoses once."""
    rig = Babysit(tmp_path)
    rig.fail_ci()
    rig.agent.maintainer.classification = "transient"
    rig.close_after(3)

    code = rig.babysit()

    assert code == 0
    assert rig.host.retries == [10]
    assert rig.agent.modes == ["diagnose"]


def test_the_rerun_budget_of_a_head_blocks_the_pull_request_without_another_rerun(tmp_path: Path) -> None:
    """Given one allowed rerun and a failure that comes back after it, when the pull request is babysat, then it blocks, the run is handed off and no second rerun is made."""
    rig = Babysit(tmp_path, retries=1)
    rig.agent.maintainer.classification = "transient"
    attempts = [1]
    rig.fail_ci(attempt=1)

    def on_wait(seconds: float) -> None:
        attempts.append(attempts[-1] + 1)
        rig.fail_ci(attempt=attempts[-1])

    rig.on_wait = on_wait

    code = rig.babysit()

    assert code == 1 and rig.status().status == "handed_off"
    assert rig.host.retries == [10]
    assert "already started 1 of its 1 attempts" in (rig.status().reason or "")


def test_a_failing_baseline_while_ci_is_failing_maintains_ci_instead_of_addressing_feedback(tmp_path: Path) -> None:
    """Given feedback, failing CI and a baseline check that fails, when the poll runs, then the maintainer fixes CI and the feedback is not addressed in that poll."""
    rig = addressing(tmp_path, build=FAILS_UNTIL_FIXED)
    rig.fail_ci_until_fixed()

    code = rig.babysit()

    assert code == 0
    assert rig.agent.modes == ["diagnose", "fix"]
    assert rig.commits() == ["babysit: fix CI (PR #7)"] and rig.replies() == []


def test_a_failing_baseline_with_green_ci_blocks_the_pull_request(tmp_path: Path) -> None:
    """Given feedback and a failing baseline with green CI, when the pull request is babysat, then it blocks without an agent run or a push."""
    rig = addressing(tmp_path, build=FAILS_UNTIL_FIXED)

    code = rig.babysit()

    assert code == 1 and rig.status().status == "handed_off"
    assert rig.agent.modes == [] and not rig.pushed() and rig.replies() == []


def test_an_invalid_agent_report_blocks_the_pull_request_without_publishing(tmp_path: Path) -> None:
    """Given an agent that answers with no JSON, when the pull request is babysat, then it blocks and nothing is pushed or replied."""
    rig = addressing(tmp_path)
    rig.agent.address = Garbage()

    code = rig.babysit()

    assert code == 1 and rig.status().status == "handed_off"
    assert rig.replies() == [] and not rig.pushed()


def test_a_blocked_run_records_the_fingerprint_of_the_pull_request_and_the_reason_on_the_run(tmp_path: Path) -> None:
    """Given a block, when the run ends, then the closed run carries the fingerprint computed from a fresh read and the reason."""
    rig = addressing(tmp_path)
    rig.agent.address = Garbage()

    rig.babysit()

    status = rig.status()
    assert status is not None and status.attributes[FINGERPRINT] == fingerprint(rig.polled())
    assert status.attributes[REASON] and status.reason == status.attributes[REASON]


def test_a_failed_verification_keeps_the_edits_blocks_the_pull_request_and_commits_nothing(tmp_path: Path) -> None:
    """Given a fix whose verification fails, when the pull request is babysat, then the edits stay in the recovery worktree, the pull request blocks with its fingerprint and nothing is committed, pushed or replied."""
    rig = addressing(tmp_path, build=FAILS_AFTER_EDIT)

    code = rig.babysit()

    assert code == 1 and rig.status().status == "handed_off"
    assert rig.status().attributes[FINGERPRINT] == fingerprint(rig.polled())
    assert (rig.worktree() / "cart.py").read_text() == "cart.py\n"
    assert rig.commits() == [] and not rig.pushed() and rig.replies() == []


def test_a_blocked_pull_request_runs_no_agent_and_pushes_nothing_until_released_then_resumes_without_asking_the_agent_again(tmp_path: Path) -> None:
    """Given a pull request blocked by a refused push, when it runs again unchanged it stays blocked with no agent, push or reply; after a release it resumes, publishes the kept commit and replies once without another agent run."""
    rig = addressing(tmp_path)
    rig.git_client = RejectPush(rig.runner)
    rig.restart()
    assert rig.babysit() == 1
    asked = list(rig.agent.modes)

    rig.restart()
    held = rig.babysit()

    assert held == 1 and rig.agent.modes == asked == ["group", "resolve"]
    assert not rig.pushed() and rig.replies() == [] and rig.status().status == "handed_off"

    rig.ledger.release("pr_babysit", rig.pr.id, "a person looked")
    rig.git_client = LocalOrigin(rig.runner)
    rig.restart()
    resumed = rig.babysit()

    assert resumed == 0 and rig.agent.modes == asked
    assert rig.pushed() and rig.replies() == [reply_text(KEY)] and rig.commits() == ["babysit: address i1 (PR #7)"]
    assert rig.status().epoch == 2


def test_a_pull_request_by_another_author_is_not_maintained(tmp_path: Path) -> None:
    """Given a pull request whose author is not the expected one, when it is babysat, then the run blocks."""
    rig = addressing(tmp_path)
    rig.pull.author = "someone-else"

    code = rig.babysit()

    assert code == 1 and rig.status().status == "handed_off" and rig.agent.modes == []


def test_a_checkout_whose_origin_is_another_repository_blocks_before_anything_is_read(tmp_path: Path) -> None:
    """Given a checkout whose origin is not the pull request's repository, when it is babysat, then the run blocks without an agent run."""
    rig = addressing(tmp_path, local_origin=False)

    code = rig.babysit()

    assert code == 1 and rig.status().status == "handed_off" and rig.agent.modes == []


def test_a_green_pull_request_receives_late_feedback_without_stopping(tmp_path: Path) -> None:
    """Given a green pull request, when feedback arrives between polls, then it is addressed in the next poll and the run goes on to completion."""
    rig = Babysit(tmp_path, threads=[])
    rig.agent.address.handlers["i1"] = answer("fix-needed", edit="cart.py")
    waited = []

    def on_wait(seconds: float) -> None:
        waited.append(seconds)
        if len(waited) == 1:
            rig.pull.threads = review()
        else:
            rig.pull.close()

    rig.on_wait = on_wait

    code = rig.babysit()

    assert code == 0 and rig.replies() == [reply_text(KEY)] and rig.agent.modes == ["group", "resolve"]


def test_a_copilot_review_is_requested_once_per_head(tmp_path: Path) -> None:
    """Given Copilot reviews on, when the pull request is polled several times on one head, then exactly one request is made."""
    rig = Babysit(tmp_path, copilot_review=True)
    rig.close_after(3)

    code = rig.babysit()

    assert code == 0 and rig.host.requested == [7]
    assert [(event.head, event.outcome) for event in rig.history(CopilotRequested)] == [(rig.head, "requested")]


def test_readiness_is_reported_when_it_changes_and_a_quiet_poll_exports_and_writes_nothing(tmp_path: Path) -> None:
    """Given a green pull request polled five times, when readiness never changes after the first report, then only that poll exports spans and writes scope records."""
    rig = Babysit(tmp_path)
    rig.close_after(5)

    code = rig.babysit()

    reports = [(event.head, event.ready) for event in rig.history(ReadinessReported)]
    exported = [span.name for span in rig.spans.tree()[0].children]
    scopes = [record["path"] for record in rig.ledger.item("pr_babysit", rig.pr.id).records() if record["kind"] == "scope-opened"]
    assert code == 0 and reports == [(rig.head, True)]
    assert exported[0] == "poll poll-1" and "poll poll-2" not in exported and "poll poll-3" not in exported
    assert scopes == [["poll", "poll-1"], ["poll", "poll-1", "ready", "ReadinessCheck"]]
    assert "replayed" not in rig.kinds()


def test_a_stop_signal_interrupts_the_run_between_polls_and_exits_130(tmp_path: Path) -> None:
    """Given a stop signal that turns on after two polls, when the pull request is babysat, then the run ends interrupted and the command exits 130."""
    rig = Babysit(tmp_path)
    rig.on_wait = rig.waits.append
    rig.stop = StopAfter(2)
    rig.restart()

    code = rig.babysit()

    assert code == 130 and rig.status().status == "interrupted"


def test_a_keyboard_interrupt_exits_130(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Given an interrupt that reaches the command mid-run, when it is caught, then the command exits 130."""
    rig = Babysit(tmp_path)

    def interrupted(*args: object, **kwargs: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(entry, "run", interrupted)

    assert rig.babysit() == 130

"""Adoption of the earlier version's per-PR state: an open batch resumes with its finished issues replayed, rerun budgets, Copilot requests and handled feedback carry over, and it happens once."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from loop.shared.github.pull_requests.babysit import digest
from tests.runtime.harness import command
from workflows.tests.software_factory.stages.pr_support import NUMBER, SLUG, answer, reply_text, selected_batch, thread
from loop import Ledger, WorkItemLog
from workflows.software_factory.workflows.pr_babysit.legacy import adopt_v1, state_path
from workflows.software_factory.workflows.pr_babysit.policy import ADOPTED, FINGERPRINT

from .golden import GOLDEN, OPEN, build_host
from .harness import Babysit
from .test_intake import Discovery

KEYS = ("thread:t1:t1c", "thread:t2:t2c")
SUBJECT = "babysit: address i1 (PR #7)"


def two_threads() -> list[dict]:
    return [thread("t1", "Concern 1"), thread("t2", "Concern 2")]


def write_state(rig: Babysit, **values) -> Path:
    """Saves a version-1 per-PR state of the rig's pull request, the way the earlier version did."""
    state = {
        "version": 1, "repo": SLUG, "number": NUMBER, "status": "running", "head": rig.head, "items": {}, "retries": {},
        "copilot": {}, "batch": None, **values,
    }
    path = state_path(rig.tmp_path, SLUG, NUMBER)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    return path


def seen_items(rig: Babysit, *, status: str = "pending", handled: tuple[str, ...] = ()) -> dict:
    """The feedback items the earlier version had persisted for the rig's threads."""
    return {item["key"]: {**item, "status": "handled" if item["key"] in handled else status} for item in selected_batch(rig).items}


def batch(rig: Babysit, *, index: int, head: str | None = None, **values) -> dict:
    """An open address batch of two issues, one per thread, as the earlier version checkpointed it."""
    issues = [{"id": f"i{n}", "statement": f"concern {n}", "keys": [key]} for n, key in enumerate(KEYS, 1)]
    return {
        "kind": "address", "base": rig.head, "head": head or rig.head, "branch": "feature", "issues": issues, "index": index,
        "replies": {}, "inflight": None, "pushed": False, "unclear": False, **values,
    }


def keep_commit(rig: Babysit, subject: str, name: str = "fix1.txt") -> str:
    """Leaves a commit on the recovery branch, as a run that died after committing would have."""
    command(("worktree", "add", "-q", "-b", rig.recovery_branch(), str(rig.worktree()), rig.head), rig.repo.path)
    (rig.worktree() / name).write_text("fixed\n")
    command(("add", name), rig.worktree())
    command(("commit", "-q", "-m", subject), rig.worktree())
    sha = command(("rev-parse", "HEAD"), rig.worktree()).strip()
    command(("worktree", "remove", "--force", str(rig.worktree())), rig.repo.path)
    return sha


def addressing(tmp_path: Path) -> Babysit:
    rig = Babysit(tmp_path, threads=two_threads())
    rig.agent.address.handlers["i2"] = answer("fix-needed", edit="fix2.txt")
    return rig


def test_an_open_batch_resumes_with_its_finished_issue_replayed_and_every_reply_posted_once(tmp_path: Path) -> None:
    """Given a state with an open batch whose first issue is finished and committed, when the first run starts, then the agent is asked only for the second issue, the commits are pushed and each reply is posted once."""
    rig = addressing(tmp_path)
    kept = keep_commit(rig, SUBJECT)
    write_state(rig, items=seen_items(rig), batch=batch(rig, index=1, head=kept, replies={KEYS[0]: "Fixed 1"}))

    code = rig.babysit()

    assert code == 0 and rig.agent.modes == ["resolve"]
    assert rig.replies() == ["Fixed 1", reply_text(KEYS[1])]
    assert rig.commits() == [SUBJECT, "babysit: address i2 (PR #7)"]
    assert rig.pushed() and [request["issue"]["id"] for request in rig.agent.address.requests if request["mode"] == "resolve"] == ["i2"]


def test_an_interrupted_commit_that_landed_is_adopted_and_the_batch_goes_on(tmp_path: Path) -> None:
    """Given a state whose commit was in flight and whose commit is on the recovery branch, when the first run starts, then the commit is adopted without asking the agent and the batch goes on."""
    rig = addressing(tmp_path)
    keep_commit(rig, SUBJECT)
    write_state(
        rig, items=seen_items(rig),
        batch=batch(rig, index=0, inflight={"parent": rig.head, "message": SUBJECT}, replies={KEYS[0]: "Fixed 1"}),
    )

    code = rig.babysit()

    assert code == 0 and rig.agent.modes == ["resolve"]
    assert rig.commits() == [SUBJECT, "babysit: address i2 (PR #7)"]
    assert rig.replies() == ["Fixed 1", reply_text(KEYS[1])]


def test_an_interrupted_commit_that_never_landed_is_held_for_inspection_and_nothing_runs_or_is_pushed(tmp_path: Path) -> None:
    """Given a state whose commit was in flight and whose worktree still holds the unverified edit, when the first run starts, then the pull request is held: no agent, no push, no reply, and the edit is kept."""
    rig = addressing(tmp_path)
    rig.leave_untracked_edit()
    write_state(
        rig, items=seen_items(rig),
        batch=batch(rig, index=0, inflight={"parent": rig.head, "message": SUBJECT}, replies={KEYS[0]: "Fixed 1"}),
    )

    code = rig.babysit()

    assert code == 1 and rig.status().status == "handed_off"
    assert rig.agent.modes == [] and not rig.pushed() and rig.replies() == []
    assert (rig.worktree() / "junk.txt").exists()


def test_a_blocked_state_with_a_batch_stays_blocked_until_the_pull_request_changes_or_is_retried(tmp_path: Path) -> None:
    """Given a blocked state with an open batch, when adopted, then the run resumes the batch as it was (held, not released) with the fingerprint the earlier version recorded."""
    rig = addressing(tmp_path)
    kept = keep_commit(rig, SUBJECT)
    write_state(rig, status="blocked", reason="Interrupted", fingerprint="old", items=seen_items(rig), batch=batch(rig, index=1, head=kept, replies={KEYS[0]: "Fixed 1"}))

    code = rig.babysit()

    assert code == 0 and rig.agent.modes == ["resolve"] and not rig.status().released
    assert rig.status().epoch == 1


def test_the_reruns_remaining_for_the_head_are_honoured(tmp_path: Path) -> None:
    """Given two of three reruns spent on the head, when the first run starts on a transient failure, then one more rerun is made."""
    rig = Babysit(tmp_path)
    rig.fail_ci()
    rig.agent.maintainer.classification = "transient"
    write_state(rig, retries={rig.head: 2})

    code = rig.babysit()

    assert code == 0 and rig.host.retries == [10]


def test_a_spent_rerun_budget_blocks_without_another_rerun(tmp_path: Path) -> None:
    """Given all three reruns spent on the head, when the first run starts on a transient failure, then no rerun is made and the pull request blocks."""
    rig = Babysit(tmp_path)
    rig.fail_ci()
    rig.agent.maintainer.classification = "transient"
    write_state(rig, retries={rig.head: 3})

    code = rig.babysit()

    assert code == 1 and rig.host.retries == [] and rig.status().status == "handed_off"


def test_a_failure_already_rerun_is_neither_diagnosed_nor_rerun_again(tmp_path: Path) -> None:
    """Given a rerun waiting on the failure's CI-run signal, when the first run starts on the same failure, then the agent is not asked and nothing is rerun."""
    rig = Babysit(tmp_path)
    rig.fail_ci()
    rig.agent.maintainer.classification = "transient"
    rig.close_after(2)
    write_state(rig, retries={rig.head: 1}, retry_wait={rig.head: digest([(10, 1)])})

    code = rig.babysit()

    assert code == 0 and rig.agent.modes == [] and rig.host.retries == []


def test_a_copilot_review_already_requested_for_the_head_is_not_requested_again(tmp_path: Path) -> None:
    """Given a Copilot request recorded for the head, when the first run starts, then none is made."""
    rig = Babysit(tmp_path, copilot_review=True)
    write_state(rig, copilot={rig.head: "requested"})

    code = rig.babysit()

    assert code == 0 and rig.host.requested == []


def test_feedback_the_earlier_version_handled_is_not_addressed_again(tmp_path: Path) -> None:
    """Given feedback recorded as handled, when the first run starts on a pull request that still shows it, then no agent is asked and nothing is replied."""
    rig = addressing(tmp_path)
    write_state(rig, items=seen_items(rig, handled=KEYS))

    code = rig.babysit()

    assert code == 0 and rig.agent.modes == [] and rig.replies() == []


def test_a_committed_ci_fix_is_published_without_diagnosing_or_fixing_again(tmp_path: Path) -> None:
    """Given a CI batch whose fix is committed but unpublished, when the first run starts, then the kept commit is pushed and no agent is asked."""
    rig = Babysit(tmp_path)
    rig.fail_ci_until_fixed()
    kept = keep_commit(rig, "babysit: fix CI (PR #7)")
    write_state(rig, batch={
        "kind": "ci", "base": rig.head, "head": kept, "branch": "feature", "index": 1, "replies": {}, "inflight": None,
    })

    code = rig.babysit()

    assert code == 0 and rig.agent.modes == [] and rig.remote_head("feature") == kept


def test_the_state_is_adopted_once_and_the_old_file_stays(tmp_path: Path) -> None:
    """Given a state adopted by a first run, when a second run starts, then the ledger is not seeded again and the state file is untouched."""
    rig = Babysit(tmp_path)
    path = write_state(rig, copilot={rig.head: "requested"})
    before = path.read_bytes()

    rig.babysit()
    rig.pull.state = "open"
    rig.restart()
    rig.babysit()

    assert rig.kinds().count("adopted") == 1 and path.read_bytes() == before


def test_adoption_never_waits_on_the_lock_of_a_live_run(tmp_path: Path) -> None:
    """Given a state file and another process holding the work item's lock, when adoption is attempted, then it returns at once without seeding the ledger."""
    rig = Babysit(tmp_path)
    path = write_state(rig)
    log = rig.ledger.item("pr_babysit", rig.pr.id)
    outcome: list[bool] = []

    with log.lock():
        attempt = threading.Thread(target=lambda: outcome.append(adopt_v1(path, rig.ledger, rig.pr, ci_signal=lambda: "")), daemon=True)
        attempt.start()
        attempt.join(timeout=5)

    assert not attempt.is_alive() and outcome == [False] and not log.records()


def test_adoption_skips_a_ledger_that_already_holds_records_without_locking(tmp_path: Path) -> None:
    """Given a state already adopted and a live run holding the lock, when the next scan attempts adoption, then it returns at once."""
    rig = Babysit(tmp_path)
    path = write_state(rig)
    assert adopt_v1(path, rig.ledger, rig.pr, ci_signal=lambda: "")
    log = rig.ledger.item("pr_babysit", rig.pr.id)
    outcome: list[bool] = []

    with log.lock():
        attempt = threading.Thread(target=lambda: outcome.append(adopt_v1(path, rig.ledger, rig.pr, ci_signal=lambda: "")), daemon=True)
        attempt.start()
        attempt.join(timeout=5)

    assert not attempt.is_alive() and outcome == [False]


def test_adoption_never_overwrites_records_another_process_wrote_after_the_unlocked_check(tmp_path: Path, monkeypatch) -> None:
    """Given a run that writes the log between adoption's unlocked check and its lock, when adoption goes on, then it sees those records and seeds nothing."""
    rig = Babysit(tmp_path)
    path = write_state(rig)
    original = WorkItemLog.lock
    written: list[bool] = []

    def lock_after_a_run(self, *, wait: bool = False):
        if not written:
            written.append(True)
            other = Ledger(rig.ledger._root).item("pr_babysit", rig.pr.id)
            with original(other, wait=True):
                other.seed([{"kind": "run-opened", "epoch": 1}, {"kind": "run-closed", "status": "completed", "reason": None, "attributes": {}}])
        return original(self, wait=wait)

    monkeypatch.setattr(WorkItemLog, "lock", lock_after_a_run)

    adopted = adopt_v1(path, rig.ledger, rig.pr, ci_signal=lambda: "")

    kinds = [record["kind"] for record in Ledger(rig.ledger._root).item("pr_babysit", rig.pr.id).records()]
    assert adopted is False and kinds == ["run-opened", "run-closed"]


def test_a_pull_request_with_no_earlier_state_adopts_nothing(tmp_path: Path) -> None:
    """Given no state file, when the first run starts, then nothing is adopted."""
    rig = Babysit(tmp_path)

    rig.babysit()

    assert "adopted" not in rig.kinds()


def test_the_golden_blocked_state_carries_its_status_and_fingerprint_into_the_ledger(tmp_path: Path) -> None:
    """Given the state the earlier version saved for the golden pull request, when discovery meets it, then the ledger shows it handed off at that fingerprint and no worker starts for the unchanged pull request."""
    host = build_host()
    host.pulls.pop(3)
    discovery = Discovery(tmp_path, host)
    golden = json.loads(GOLDEN.read_text())["open"]
    path = state_path(tmp_path, "a/b", OPEN)
    path.parent.mkdir(parents=True)
    path.write_text(golden["state_file"])
    discovery.serve(1, "--include-existing")

    discovery.serve(3)

    status = discovery.ledger.status("pr_babysit", f"a/b#{OPEN}")
    assert status is not None and status.status == "handed_off" and status.attributes[FINGERPRINT] == golden["fingerprint"]
    assert status.attributes[ADOPTED] is True and discovery.children.started == []
    assert path.read_text() == golden["state_file"]


def test_a_pull_request_the_earlier_baseline_ignored_stays_ignored(tmp_path: Path) -> None:
    """Given a registry of the earlier version whose pull request state is ignored, when discovery runs with an open pull request, then it gets no worker."""
    discovery = Discovery(tmp_path)
    (tmp_path / "scheduler.json").write_text(json.dumps({
        "version": 1, "initialized": True, "author": "me", "repositories": ["a/b"],
        "prs": {"a/b#1": {"repo": "a/b", "number": 1, "url": "https://github.com/a/b/pull/1"}},
    }))
    path = state_path(tmp_path, "a/b", 1)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "repo": "a/b", "number": 1, "status": "ignored", "items": {}, "retries": {}, "copilot": {}, "batch": None}))

    discovery.serve(3)

    assert discovery.children.started == []

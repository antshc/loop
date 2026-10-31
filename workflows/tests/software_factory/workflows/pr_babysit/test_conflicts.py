"""Conflict recovery through the CLI, using real merges and a local bare remote."""

from __future__ import annotations

from pathlib import Path

import pytest
from loop.shared.process import CommandError

from tests.runtime.harness import command
from workflows.software_factory.events.pull_requests import MergePublished
from workflows.software_factory.workflows.pr_babysit.policy import fingerprint
from workflows.tests.software_factory.stages.pr_support import Crash, answer, thread

from .harness import Babysit, LocalOrigin


def conflicting(tmp_path: Path, **options) -> Babysit:
    """A feature adding cart.py against a release branch adding different content."""
    rig = Babysit(tmp_path, **options)
    command(("checkout", "-q", "-b", "release"), rig.repo.path)
    rig.repo.write("cart.py", "base cart\n")
    rig.repo.commit("base change")
    command(("push", "-q", "origin", "release"), rig.repo.path)
    rig.pull.base_ref = "release"
    rig.pull.base_sha = lambda: rig.remote_head("release")
    rig.pull.mergeable = False
    rig.pull.mergeable_state = "dirty"

    def resolve(path: Path) -> dict:
        assert "<<<<<<<" in (path / "cart.py").read_text()
        (path / "cart.py").write_text("combined cart\n")
        return {"verdict": "fixed", "reason": "preserved both changes"}

    rig.agent.maintainer.fix = resolve
    return rig


def parents(rig: Babysit) -> list[str]:
    """The parents of the published PR head."""
    return command(
        ("rev-list", "--parents", "-n", "1", rig.remote_head("feature")),
        rig.repo.path,
    ).split()[1:]


def test_conflict_precedes_feedback_and_ci_and_publishes_a_verified_merge(tmp_path):
    """Conflict repair takes priority and publishes a two-parent merge of release."""
    rig = conflicting(tmp_path, threads=[thread("t1", "fix this")])
    rig.fail_ci()
    base = rig.remote_head("release")

    assert rig.babysit() == 0

    assert rig.agent.modes == ["resolve-conflicts"]
    assert parents(rig) == [rig.head, base]
    assert rig.replies() == []
    assert rig.history(MergePublished)[0].head == rig.remote_head("feature")
    assert not rig.worktree().exists()


@pytest.mark.parametrize("outcome", ["blocked", "unchanged", "red"])
def test_unresolved_or_unverified_work_is_retained_without_publication(
    tmp_path, outcome
):
    """Blocked resolutions, remaining markers and failed checks retain the worktree."""
    rig = conflicting(
        tmp_path, build="raise SystemExit(1)" if outcome == "red" else None
    )
    if outcome != "red":
        rig.agent.maintainer.fix = lambda _: {
            "verdict": "blocked" if outcome == "blocked" else "fixed"
        }

    assert rig.babysit() == 1

    assert not rig.pushed()
    assert rig.status().status == "handed_off"
    assert rig.worktree().exists()
    assert command(
        ("rev-parse", "MERGE_HEAD"), rig.worktree()
    ).strip() == rig.remote_head("release")


def test_unknown_mergeability_waits_without_running_any_agent(tmp_path):
    """Unknown mergeability waits even when trusted feedback is pending."""
    rig = conflicting(tmp_path, threads=[thread("t1", "fix this")])
    rig.pull.mergeable = None
    rig.pull.mergeable_state = "unknown"

    assert rig.babysit() == 0
    assert rig.agent.modes == [] and not rig.pushed()


def test_stale_conflict_signal_can_merge_cleanly_without_an_agent(tmp_path):
    """A clean merge commits even when its tree equals the feature tree."""
    rig = conflicting(tmp_path)
    rig.repo.write("cart.py", "cart\n")
    rig.repo.commit("same content")
    command(("push", "-q", "origin", "release"), rig.repo.path)

    assert rig.babysit() == 0

    assert rig.agent.modes == []
    assert parents(rig) == [rig.head, rig.remote_head("release")]


def test_interrupted_push_adopts_the_exact_merge_without_resolving_again(tmp_path):
    """A completed push is reconciled without another resolver or commit."""
    rig = conflicting(tmp_path)

    class InterruptedPush(LocalOrigin):
        def publish(self, path, branch):
            super().publish(path, branch)
            raise Crash("after push")

    rig.git_client = InterruptedPush(rig.runner)
    rig.restart()
    with pytest.raises(Crash):
        rig.babysit()
    published = rig.remote_head("feature")

    rig.git_client = LocalOrigin(rig.runner)
    rig.restart()
    assert rig.babysit() == 0
    assert rig.remote_head("feature") == published
    assert rig.agent.modes == ["resolve-conflicts"]


def test_moving_base_during_resolution_blocks_publication(tmp_path):
    """A concurrent base update invalidates the pinned merge before publication."""
    rig = conflicting(tmp_path)
    resolve = rig.agent.maintainer.fix

    def moved(path):
        reply = resolve(path)
        rig.repo.write("later.txt", "new base\n")
        rig.repo.commit("base moved")
        command(("push", "-q", "origin", "release"), rig.repo.path)
        return reply

    rig.agent.maintainer.fix = moved

    assert rig.babysit() == 1
    assert not rig.pushed() and rig.worktree().exists()


@pytest.mark.parametrize("after", [False, True])
def test_interrupted_commit_resumes_without_another_agent_or_merge(tmp_path, after):
    """Before and after commit crashes adopt only the recorded verified tree."""
    rig = conflicting(tmp_path)

    class InterruptedCommit(LocalOrigin):
        def commit(self, path, subject, body=""):
            if after:
                super().commit(path, subject, body)
            raise Crash("during commit")

    rig.git_client = InterruptedCommit(rig.runner)
    rig.restart()
    with pytest.raises(Crash):
        rig.babysit()

    rig.git_client = LocalOrigin(rig.runner)
    rig.restart()
    assert rig.babysit() == 0
    assert rig.agent.modes == ["resolve-conflicts"]
    assert parents(rig) == [rig.head, rig.remote_head("release")]


def test_rejected_push_can_publish_the_retained_merge_after_release(tmp_path):
    """A released publication failure reuses the verified local merge commit."""
    rig = conflicting(tmp_path)

    class RejectPush(LocalOrigin):
        def publish(self, path, branch):
            raise CommandError("git push", 1, "rejected")

    rig.git_client = RejectPush(rig.runner)
    rig.restart()
    assert rig.babysit() == 1 and not rig.pushed()
    kept = command(("rev-parse", rig.recovery_branch()), rig.repo.path).strip()
    rig.ledger.release("pr_babysit", rig.pr.id, "inspect publication")
    rig.git_client = LocalOrigin(rig.runner)
    rig.restart()

    assert rig.babysit() == 0
    assert rig.remote_head("feature") == kept
    assert rig.agent.modes == ["resolve-conflicts"]


def test_dirty_recovery_work_is_preserved_without_starting_a_merge(tmp_path):
    """Preexisting edits prevent any merge or agent work."""
    rig = conflicting(tmp_path)
    rig.leave_untracked_edit()

    assert rig.babysit() == 1
    assert (rig.worktree() / "junk.txt").read_text() == "unrelated\n"
    assert not rig.pushed() and rig.agent.modes == []


def test_git_failure_is_not_treated_as_a_conflict(tmp_path, monkeypatch):
    """An operational merge failure blocks without asking the resolver to guess."""
    rig = conflicting(tmp_path)
    original = rig.git_client.text

    def fail_merge(path, *args):
        if args[0] == "merge":
            raise CommandError("git merge", 128, "cannot lock index")
        return original(path, *args)

    monkeypatch.setattr(rig.git_client, "text", fail_merge)
    assert rig.babysit() == 1
    assert rig.agent.modes == [] and not rig.pushed()


def test_interrupted_merge_is_retained_for_inspection(tmp_path, monkeypatch):
    """An interrupted merge is never silently retried over dirty work."""
    rig = conflicting(tmp_path)
    original = rig.git_client.text

    def crash_merge(path, *args):
        if args[0] != "merge":
            return original(path, *args)
        try:
            original(path, *args)
        except CommandError:
            raise Crash("merge started") from None
        raise Crash("merge started")

    monkeypatch.setattr(rig.git_client, "text", crash_merge)
    with pytest.raises(Crash):
        rig.babysit()
    monkeypatch.setattr(rig.git_client, "text", original)
    rig.restart()

    assert rig.babysit() == 1
    assert rig.worktree().exists() and not rig.pushed()
    assert rig.agent.modes == []


def test_moving_head_during_resolution_stops_before_push(tmp_path):
    """Concurrent PR updates are preserved and the stale merge is not published."""
    rig = conflicting(tmp_path)
    resolve = rig.agent.maintainer.fix
    advanced = []

    def moved(path):
        reply = resolve(path)
        command(("checkout", "-q", "feature"), rig.repo.path)
        rig.repo.write("concurrent.txt", "other author\n")
        advanced.append(rig.repo.commit("concurrent update"))
        command(("push", "-q", "origin", "feature"), rig.repo.path)
        return reply

    rig.agent.maintainer.fix = moved

    assert rig.babysit() == 1
    assert rig.remote_head("feature") == advanced[0]
    assert rig.worktree().exists()


def test_unexpected_agent_commit_is_rejected(tmp_path):
    """An agent moving HEAD cannot publish even when it made a valid merge."""
    rig = conflicting(tmp_path)
    resolve = rig.agent.maintainer.fix

    def commits(path):
        reply = resolve(path)
        command(("add", "-A"), path)
        command(("commit", "-qm", "agent commit"), path)
        return reply

    rig.agent.maintainer.fix = commits

    assert rig.babysit() == 1
    assert not rig.pushed()
    assert (
        command(
            ("log", "-1", "--format=%s", rig.recovery_branch()), rig.repo.path
        ).strip()
        == "agent commit"
    )


def test_repeated_stale_conflict_snapshots_do_not_create_duplicate_merges(tmp_path):
    """Once the base is included, repeated stale snapshots need no agent or commit."""
    rig = conflicting(tmp_path)
    rig.close_after(3)

    assert rig.babysit() == 0
    assert rig.agent.modes == ["resolve-conflicts"]
    assert (
        command(
            (
                "rev-list",
                "--count",
                "--merges",
                f"{rig.head}..{rig.remote_head('feature')}",
            ),
            rig.repo.path,
        ).strip()
        == "1"
    )


def test_next_poll_handles_feedback_on_the_new_head(tmp_path):
    """Successful conflict recovery returns to normal review handling."""
    rig = conflicting(tmp_path)
    rig.agent.address.handlers["i1"] = answer("fix-needed", edit="followup.txt")
    waited = []

    def on_wait(seconds):
        waited.append(seconds)
        if len(waited) == 1:
            rig.pull.mergeable = True
            rig.pull.mergeable_state = "clean"
            rig.pull.threads = [thread("t1", "fix this")]
        else:
            rig.pull.close()

    rig.on_wait = on_wait

    assert rig.babysit() == 0
    assert rig.agent.modes == ["resolve-conflicts", "group", "resolve"]
    assert rig.replies() == ["Done: thread:t1:t1c"]


def test_base_only_changes_extend_the_fingerprint(tmp_path):
    """Base changes affect new blocked runs without changing the legacy component."""
    rig = conflicting(tmp_path)
    before = fingerprint(rig.polled())
    rig.repo.write("later.txt", "base change\n")
    rig.repo.commit("new base")
    command(("push", "-q", "origin", "release"), rig.repo.path)
    after = fingerprint(rig.polled())

    assert before != after
    assert before.split(":")[0] == after.split(":")[0]


def test_a_commit_hook_cannot_publish_an_unverified_tree(tmp_path):
    """A commit-time edit invalidates the verified tree and stops publication."""
    rig = conflicting(tmp_path)

    class HookEdits(LocalOrigin):
        def commit(self, path, subject, body=""):
            (path / "hook.txt").write_text("not verified\n")
            return super().commit(path, subject, body)

    rig.git_client = HookEdits(rig.runner)
    rig.restart()

    assert rig.babysit() == 1
    assert not rig.pushed()
    assert (
        command(("show", f"{rig.recovery_branch()}:hook.txt"), rig.repo.path).strip()
        == "not verified"
    )


def test_an_interrupted_commit_rejects_edits_added_after_verification(tmp_path):
    """Recovery cannot commit edits introduced after the recorded verification."""
    rig = conflicting(tmp_path)

    class InterruptedCommit(LocalOrigin):
        def commit(self, path, subject, body=""):
            (path / "later.txt").write_text("not verified\n")
            raise Crash("before commit")

    rig.git_client = InterruptedCommit(rig.runner)
    rig.restart()
    with pytest.raises(Crash):
        rig.babysit()

    rig.git_client = LocalOrigin(rig.runner)
    rig.restart()

    assert rig.babysit() == 1
    assert not rig.pushed()
    assert (rig.worktree() / "later.txt").read_text() == "not verified\n"

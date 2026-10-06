from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

import pytest

from orb import (
    Cancelled,
    CommandResult,
    Hook,
    HookError,
    NoCapsule,
    OrbError,
    SandboxHooks,
    create_sandbox,
    with_sandbox_lifecycle,
)
from orb.testing import FakeAgentClient, FakeGitClient

CHECKOUT = Path("/repo")
HARNESS = Path("/harness")


class _Capsule(NoCapsule):
    """A NoCapsule that records `exec` commands, can pose as isolated, and can fail them with scripted exits."""

    def __init__(self, *, isolated: bool = False, exits: Sequence[int] = ()) -> None:
        self.commands: list[str] = []
        self.closed = False
        self._isolated = isolated
        self._exits = list(exits)
        super().__init__(HARNESS, executor=self._execute)

    @property
    def isolated(self) -> bool:
        return self._isolated

    def close(self) -> None:
        self.closed = True

    def _execute(self, command, *, timeout_s=None, on_line=None, cancel=None) -> CommandResult:
        self.commands.append(command)
        return CommandResult(self._exits.pop(0) if self._exits else 0, "", "boom")


def _sandbox(git: FakeGitClient, capsule: _Capsule, **kwargs):
    return create_sandbox(
        git,
        lambda workspace, cancel: capsule,
        checkout=CHECKOUT,
        harness_root=HARNESS,
        base="main",
        **kwargs,
    )


def test_create_sandbox_runs_worktree_ready_then_sandbox_ready_hooks_on_a_generated_branch() -> None:
    git = FakeGitClient()
    hooks = SandboxHooks((Hook("a"),), (Hook("b"),))

    sandbox = _sandbox(git, _Capsule(), hooks=hooks)

    assert git.hook_calls == ["a", "b"]
    assert sandbox.branch.startswith("orb/sandbox-")
    assert git.worktrees == {sandbox.worktree: sandbox.branch}


def test_create_sandbox_uses_the_given_branch() -> None:
    git = FakeGitClient()

    sandbox = _sandbox(git, _Capsule(), branch="feature-x")

    assert sandbox.branch == "feature-x"


def test_create_sandbox_closes_the_capsule_and_removes_the_worktree_when_a_sandbox_ready_hook_fails() -> None:
    git = FakeGitClient()
    git.failing_hooks.add("b")
    capsule = _Capsule()

    with pytest.raises(HookError):
        _sandbox(git, capsule, branch="feature-x", hooks=SandboxHooks(sandbox_ready=(Hook("b"),)))

    assert capsule.closed
    assert git.worktrees == {}


def test_create_sandbox_keeps_a_dirty_worktree_when_cancelled_during_setup() -> None:
    git = FakeGitClient()
    git.cancelled_hooks.add("b")
    git.branches["feature-x"] = ["c1"]
    capsule = _Capsule()

    with pytest.raises(Cancelled) as raised:
        _sandbox(git, capsule, branch="feature-x", hooks=SandboxHooks(sandbox_ready=(Hook("b"),)))

    assert capsule.closed
    assert raised.value.worktree is not None and raised.value.worktree in git.worktrees


def test_create_sandbox_removes_a_clean_worktree_when_cancelled_during_setup() -> None:
    git = FakeGitClient()
    git.cancelled_hooks.add("b")

    with pytest.raises(Cancelled) as raised:
        _sandbox(git, _Capsule(), branch="feature-x", hooks=SandboxHooks(sandbox_ready=(Hook("b"),)))

    assert raised.value.worktree is None
    assert git.worktrees == {}


def test_create_sandbox_removes_the_worktree_when_the_capsule_cannot_start() -> None:
    git = FakeGitClient()

    def factory(workspace: Path, cancel: threading.Event) -> NoCapsule:
        raise OrbError("no docker")

    with pytest.raises(OrbError, match="no docker"):
        create_sandbox(git, factory, checkout=CHECKOUT, harness_root=HARNESS, base="main", branch="feature-x")

    assert git.worktrees == {}


def test_run_returns_the_agent_result_and_the_commits_the_agent_made_on_the_branch() -> None:
    git = FakeGitClient()
    sandbox = _sandbox(git, _Capsule(), branch="feature-x")
    agent = FakeAgentClient(lambda prompt, options: git.commit(sandbox.worktree, "x") and "done")

    first = sandbox.run(lambda executor: agent, "go")
    second = sandbox.run(lambda executor: agent, "go")

    assert first.result.stdout == "done" and first.branch == "feature-x"
    assert first.commits == (f"{1:040d}",) and second.commits == (f"{2:040d}",)
    assert git.merged == [] and git.deleted_branches == []


def test_run_with_merge_to_head_merges_each_run_and_keeps_the_branch() -> None:
    git = FakeGitClient()
    sandbox = _sandbox(git, _Capsule(), branch="feature-x", merge_to_head=True)
    agent = FakeAgentClient(lambda prompt, options: "done")

    sandbox.run(lambda executor: agent, "go")
    sandbox.run(lambda executor: agent, "go")

    assert git.merged == [(CHECKOUT, "feature-x")] * 2
    assert git.detached == set() and git.deleted_branches == []


def test_run_calls_apply_to_host_after_the_work_and_before_the_commits_are_collected() -> None:
    git = FakeGitClient()
    events: list[str] = []
    sandbox = _sandbox(git, _Capsule(), branch="feature-x", apply_to_host=lambda: events.append("apply"))
    agent = FakeAgentClient(lambda prompt, options: events.append("work") or "done")

    sandbox.run(lambda executor: agent, "go")

    assert events == ["work", "apply"]


def test_close_removes_the_worktree_and_closes_the_capsule_once() -> None:
    git = FakeGitClient()
    capsule = _Capsule()
    sandbox = _sandbox(git, capsule, branch="feature-x")

    sandbox.close()
    sandbox.close()

    assert capsule.closed and git.removed == [sandbox.worktree]


def test_close_keeps_the_worktree_on_request() -> None:
    git = FakeGitClient()
    sandbox = _sandbox(git, _Capsule(), branch="feature-x")

    sandbox.close(keep_worktree=True)

    assert git.removed == []


def test_leaving_the_context_cancelled_keeps_a_dirty_worktree_and_removes_a_clean_one() -> None:
    git = FakeGitClient()
    dirty = _sandbox(git, _Capsule(), branch="dirty")
    git.commit(dirty.worktree, "x")
    clean = _sandbox(git, _Capsule(), branch="clean")

    dirty.__exit__(Cancelled, Cancelled(), None)
    clean.__exit__(Cancelled, Cancelled(), None)

    assert git.removed == [clean.worktree]


def test_leaving_the_context_without_an_error_removes_the_worktree() -> None:
    git = FakeGitClient()

    with _sandbox(git, _Capsule(), branch="feature-x") as sandbox:
        pass

    assert git.removed == [sandbox.worktree]


def _worktree(git: FakeGitClient, branch: str = "tmp") -> Path:
    return git.create_worktree(CHECKOUT, branch, "main", HARNESS)


def test_lifecycle_in_temp_branch_mode_merges_into_the_host_branch_then_detaches_and_deletes_the_temp_branch() -> None:
    git = FakeGitClient()
    worktree = _worktree(git)

    outcome = with_sandbox_lifecycle(
        git, _Capsule(), CHECKOUT, worktree, lambda base_head: git.commit(worktree, "x"), branch=None
    )

    assert outcome.branch == "tmp" and outcome.commits == (f"{1:040d}",)
    assert git.merged == [(CHECKOUT, "tmp")]
    assert git.detached == {worktree} and git.deleted_branches == ["tmp"]


def test_lifecycle_with_keep_source_branch_skips_the_detach_and_delete() -> None:
    git = FakeGitClient()
    worktree = _worktree(git)

    with_sandbox_lifecycle(git, _Capsule(), CHECKOUT, worktree, lambda base_head: None, branch=None, keep_source_branch=True)

    assert git.merged == [(CHECKOUT, "tmp")]
    assert git.detached == set() and git.deleted_branches == []


def test_lifecycle_with_an_explicit_branch_neither_merges_nor_deletes() -> None:
    git = FakeGitClient()
    worktree = _worktree(git)

    with_sandbox_lifecycle(git, _Capsule(), CHECKOUT, worktree, lambda base_head: None, branch="tmp")

    assert git.merged == [] and git.deleted_branches == []


def test_lifecycle_passes_the_head_before_the_work_to_the_work() -> None:
    git = FakeGitClient()
    worktree = _worktree(git)
    git.commit(worktree, "earlier")
    seen: list[str] = []

    outcome = with_sandbox_lifecycle(
        git, _Capsule(), CHECKOUT, worktree, lambda base_head: seen.append(base_head) or git.commit(worktree, "x"), branch="tmp"
    )

    assert seen == [f"{1:040d}"] and outcome.commits == (f"{2:040d}",)


def test_lifecycle_rejects_a_temp_branch_merge_into_a_detached_host_checkout() -> None:
    git = FakeGitClient()
    git.host_branch = None
    worktree = _worktree(git)

    with pytest.raises(OrbError, match="detached HEAD"):
        with_sandbox_lifecycle(git, _Capsule(), CHECKOUT, worktree, lambda base_head: None, branch=None)


def test_lifecycle_runs_sandbox_ready_hooks_before_the_work() -> None:
    git = FakeGitClient()
    worktree = _worktree(git)
    seen: list[list[str]] = []

    with_sandbox_lifecycle(
        git,
        _Capsule(),
        CHECKOUT,
        worktree,
        lambda base_head: seen.append(list(git.hook_calls)),
        branch="tmp",
        on_sandbox_ready=(Hook("npm install"),),
    )

    assert seen == [["npm install"]]


def test_lifecycle_trusts_the_worktree_and_copies_the_host_identity_into_an_isolated_capsule() -> None:
    git = FakeGitClient()
    git.config = {"user.name": "Ada L", "user.email": "ada@example.com"}
    capsule = _Capsule(isolated=True)
    worktree = _worktree(git)

    with_sandbox_lifecycle(git, capsule, CHECKOUT, worktree, lambda base_head: None, branch="tmp")

    assert capsule.commands == [
        f"git config --global --get-all safe.directory | grep -qxF {worktree}"
        f" || git config --global --add safe.directory {worktree}",
        "git config --global user.name 'Ada L'",
        "git config --global user.email ada@example.com",
    ]


def test_lifecycle_leaves_a_host_capsule_untouched() -> None:
    git = FakeGitClient()
    git.config = {"user.name": "Ada"}
    capsule = _Capsule()

    with_sandbox_lifecycle(git, capsule, CHECKOUT, _worktree(git), lambda base_head: None, branch="tmp")

    assert capsule.commands == []


def test_lifecycle_retries_a_transient_capsule_setup_exit_then_continues() -> None:
    git = FakeGitClient()
    capsule = _Capsule(isolated=True, exits=[137, 0])
    sleeps: list[float] = []

    with_sandbox_lifecycle(
        git, capsule, CHECKOUT, _worktree(git), lambda base_head: None, branch="tmp", sleep=sleeps.append
    )

    assert len(capsule.commands) == 2 and sleeps == [0.25]


def test_lifecycle_fails_after_exhausting_retries_on_a_transient_capsule_setup_exit() -> None:
    git = FakeGitClient()
    capsule = _Capsule(isolated=True, exits=[137, 137, 137])
    sleeps: list[float] = []

    with pytest.raises(OrbError, match="137"):
        with_sandbox_lifecycle(
            git, capsule, CHECKOUT, _worktree(git), lambda base_head: None, branch="tmp", sleep=sleeps.append
        )

    assert sleeps == [0.25, 0.25]


def test_lifecycle_fails_at_once_on_a_non_transient_capsule_setup_exit() -> None:
    git = FakeGitClient()
    capsule = _Capsule(isolated=True, exits=[1])
    sleeps: list[float] = []

    with pytest.raises(OrbError):
        with_sandbox_lifecycle(
            git, capsule, CHECKOUT, _worktree(git), lambda base_head: None, branch="tmp", sleep=sleeps.append
        )

    assert sleeps == [] and len(capsule.commands) == 1

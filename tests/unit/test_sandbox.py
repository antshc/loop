from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

import pytest

from loop import (
    Branch,
    Cancelled,
    CommandResult,
    Hook,
    HookError,
    NoSandbox,
    LoopError,
    SandboxHooks,
    Worktree,
    create_sandbox,
    with_sandbox_lifecycle,
)
from loop.testing import FakeAgentClient, FakeGitClient

CHECKOUT = Path("/repo")
HARNESS = Path("/harness")


class _Sandbox(NoSandbox):
    """A NoSandbox that records `exec` commands, can pose as isolated, and can fail them with scripted exits."""

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


def _sandbox(git: FakeGitClient, sandbox: _Sandbox, tmp_path: Path, **kwargs):
    return create_sandbox(
        git,
        git.commits,
        git.worktree_service,
        git.branch_service,
        lambda workspace, cancel: sandbox,
        checkout=CHECKOUT,
        harness_root=tmp_path,
        base="main",
        **kwargs,
    )


def test_create_sandbox_runs_worktree_ready_then_sandbox_ready_hooks_on_a_generated_branch(tmp_path: Path) -> None:
    git = FakeGitClient()
    hooks = SandboxHooks((Hook("a"),), (Hook("b"),))

    sandbox = _sandbox(git, _Sandbox(), tmp_path, hooks=hooks)

    assert git.hook_calls == ["a", "b"]
    assert sandbox.branch.startswith("loop/sandbox-")
    assert git.worktrees == {sandbox.worktree.path: sandbox.branch}


def test_create_sandbox_removes_the_worktree_and_does_not_start_the_sandbox_when_a_worktree_ready_hook_fails(
    tmp_path: Path,
) -> None:
    git = FakeGitClient()
    git.failing_hooks.add("a")
    started: list[bool] = []

    with pytest.raises(HookError):
        create_sandbox(
            git,
            git.commits,
            git.worktree_service,
            git.branch_service,
            lambda workspace, cancel: started.append(True) or _Sandbox(),
            checkout=CHECKOUT,
            harness_root=tmp_path,
            base="main",
            branch="feature-x",
            hooks=SandboxHooks(worktree_ready=(Hook("a"),)),
        )

    assert git.worktrees == {}
    assert not started


def test_create_sandbox_removes_a_clean_worktree_when_cancelled_during_a_worktree_ready_hook(tmp_path: Path) -> None:
    git = FakeGitClient()
    git.cancelled_hooks.add("a")

    with pytest.raises(Cancelled) as raised:
        _sandbox(git, _Sandbox(), tmp_path, branch="feature-x", hooks=SandboxHooks(worktree_ready=(Hook("a"),)))

    assert raised.value.worktree is None
    assert git.worktrees == {}


def test_create_sandbox_keeps_a_dirty_worktree_when_cancelled_during_a_worktree_ready_hook(tmp_path: Path) -> None:
    git = FakeGitClient()
    git.cancelled_hooks.add("a")
    git.branches["feature-x"] = ["c1"]

    with pytest.raises(Cancelled) as raised:
        _sandbox(git, _Sandbox(), tmp_path, branch="feature-x", hooks=SandboxHooks(worktree_ready=(Hook("a"),)))

    assert raised.value.worktree is not None and raised.value.worktree in git.worktrees


def test_create_sandbox_uses_the_given_branch(tmp_path: Path) -> None:
    git = FakeGitClient()

    sandbox = _sandbox(git, _Sandbox(), tmp_path, branch="feature-x")

    assert sandbox.branch == "feature-x"


def test_create_sandbox_closes_the_sandbox_and_removes_the_worktree_when_a_sandbox_ready_hook_fails(
    tmp_path: Path,
) -> None:
    git = FakeGitClient()
    git.failing_hooks.add("b")
    sandbox = _Sandbox()

    with pytest.raises(HookError):
        _sandbox(git, sandbox, tmp_path, branch="feature-x", hooks=SandboxHooks(sandbox_ready=(Hook("b"),)))

    assert sandbox.closed
    assert git.worktrees == {}


def test_create_sandbox_keeps_a_dirty_worktree_when_cancelled_during_setup(tmp_path: Path) -> None:
    git = FakeGitClient()
    git.cancelled_hooks.add("b")
    git.branches["feature-x"] = ["c1"]
    sandbox = _Sandbox()

    with pytest.raises(Cancelled) as raised:
        _sandbox(git, sandbox, tmp_path, branch="feature-x", hooks=SandboxHooks(sandbox_ready=(Hook("b"),)))

    assert sandbox.closed
    assert raised.value.worktree is not None and raised.value.worktree in git.worktrees


def test_create_sandbox_removes_a_clean_worktree_when_cancelled_during_setup(tmp_path: Path) -> None:
    git = FakeGitClient()
    git.cancelled_hooks.add("b")

    with pytest.raises(Cancelled) as raised:
        _sandbox(git, _Sandbox(), tmp_path, branch="feature-x", hooks=SandboxHooks(sandbox_ready=(Hook("b"),)))

    assert raised.value.worktree is None
    assert git.worktrees == {}


def test_create_sandbox_removes_the_worktree_when_the_sandbox_cannot_start(tmp_path: Path) -> None:
    git = FakeGitClient()

    def factory(workspace: Path, cancel: threading.Event) -> NoSandbox:
        raise LoopError("no docker")

    with pytest.raises(LoopError, match="no docker"):
        create_sandbox(
            git,
            git.commits,
            git.worktree_service,
            git.branch_service,
            factory,
            checkout=CHECKOUT,
            harness_root=tmp_path,
            base="main",
            branch="feature-x",
        )

    assert git.worktrees == {}


def test_run_returns_the_agent_result_and_the_commits_the_agent_made_on_the_branch(tmp_path: Path) -> None:
    git = FakeGitClient()
    sandbox = _sandbox(git, _Sandbox(), tmp_path, branch="feature-x")
    agent = FakeAgentClient(lambda prompt, options: git.commit(sandbox.worktree.path, "x") and "done")

    first = sandbox.run(lambda executor: agent, "go")
    second = sandbox.run(lambda executor: agent, "go")

    assert first.result.stdout == "done" and first.branch == "feature-x"
    assert first.commits == (f"{1:040d}",) and second.commits == (f"{2:040d}",)
    assert git.merged == [] and git.deleted_branches == []


def test_run_with_merge_to_head_merges_each_run_and_keeps_the_branch(tmp_path: Path) -> None:
    git = FakeGitClient()
    sandbox = _sandbox(git, _Sandbox(), tmp_path, branch="feature-x", merge_to_head=True)
    agent = FakeAgentClient(lambda prompt, options: "done")

    sandbox.run(lambda executor: agent, "go")
    sandbox.run(lambda executor: agent, "go")

    assert git.merged == [(CHECKOUT, "feature-x")] * 2
    assert git.detached == set() and git.deleted_branches == []


def test_run_calls_apply_to_host_after_the_work_and_before_the_commits_are_collected(tmp_path: Path) -> None:
    git = FakeGitClient()
    events: list[str] = []
    sandbox = _sandbox(git, _Sandbox(), tmp_path, branch="feature-x", apply_to_host=lambda: events.append("apply"))
    agent = FakeAgentClient(lambda prompt, options: events.append("work") or "done")

    sandbox.run(lambda executor: agent, "go")

    assert events == ["work", "apply"]


def test_close_removes_the_worktree_and_closes_the_sandbox_once(tmp_path: Path) -> None:
    git = FakeGitClient()
    environment = _Sandbox()
    sandbox = _sandbox(git, environment, tmp_path, branch="feature-x")

    sandbox.close()
    sandbox.close()

    assert environment.closed and git.removed == [sandbox.worktree.path]


def test_close_keeps_the_worktree_on_request(tmp_path: Path) -> None:
    git = FakeGitClient()
    sandbox = _sandbox(git, _Sandbox(), tmp_path, branch="feature-x")

    sandbox.close(keep_worktree=True)

    assert git.removed == []


def test_leaving_the_context_cancelled_keeps_a_dirty_worktree_and_removes_a_clean_one(tmp_path: Path) -> None:
    git = FakeGitClient()
    dirty = _sandbox(git, _Sandbox(), tmp_path, branch="dirty")
    git.commit(dirty.worktree.path, "x")
    clean = _sandbox(git, _Sandbox(), tmp_path, branch="clean")

    dirty.__exit__(Cancelled, Cancelled(), None)
    clean.__exit__(Cancelled, Cancelled(), None)

    assert git.removed == [clean.worktree.path]


def test_leaving_the_context_without_an_error_removes_the_worktree(tmp_path: Path) -> None:
    git = FakeGitClient()

    with _sandbox(git, _Sandbox(), tmp_path, branch="feature-x") as sandbox:
        pass

    assert git.removed == [sandbox.worktree.path]


def _worktree(git: FakeGitClient, branch: str = "tmp") -> Worktree:
    return git.worktree_service.create(Branch(CHECKOUT, branch), HARNESS / branch)


def test_lifecycle_in_temp_branch_mode_merges_into_the_host_branch_then_detaches_and_deletes_the_temp_branch() -> None:
    git = FakeGitClient()
    worktree = _worktree(git)

    outcome = with_sandbox_lifecycle(
        git,
        git.commits,
        git.branch_service,
        git.worktree_service,
        _Sandbox(),
        CHECKOUT,
        worktree,
        lambda base_head: git.commit(worktree.path, "x"),
        branch=None,
    )

    assert outcome.branch == "tmp" and outcome.commits == (f"{1:040d}",)
    assert git.merged == [(CHECKOUT, "tmp")]
    assert git.detached == {worktree.path} and git.deleted_branches == ["tmp"]


def test_lifecycle_with_keep_source_branch_skips_the_detach_and_delete() -> None:
    git = FakeGitClient()
    worktree = _worktree(git)

    with_sandbox_lifecycle(
        git,
        git.commits,
        git.branch_service,
        git.worktree_service,
        _Sandbox(),
        CHECKOUT,
        worktree,
        lambda base_head: None,
        branch=None,
        keep_source_branch=True,
    )

    assert git.merged == [(CHECKOUT, "tmp")]
    assert git.detached == set() and git.deleted_branches == []


def test_lifecycle_with_an_explicit_branch_neither_merges_nor_deletes() -> None:
    git = FakeGitClient()
    worktree = _worktree(git)

    with_sandbox_lifecycle(
        git,
        git.commits,
        git.branch_service,
        git.worktree_service,
        _Sandbox(),
        CHECKOUT,
        worktree,
        lambda base_head: None,
        branch="tmp",
    )

    assert git.merged == [] and git.deleted_branches == []


def test_lifecycle_passes_the_head_before_the_work_to_the_work() -> None:
    git = FakeGitClient()
    worktree = _worktree(git)
    git.commit(worktree.path, "earlier")
    seen: list[str] = []

    outcome = with_sandbox_lifecycle(
        git,
        git.commits,
        git.branch_service,
        git.worktree_service,
        _Sandbox(),
        CHECKOUT,
        worktree,
        lambda base_head: seen.append(base_head) or git.commit(worktree.path, "x"),
        branch="tmp",
    )

    assert seen == [f"{1:040d}"] and outcome.commits == (f"{2:040d}",)


def test_lifecycle_rejects_a_temp_branch_merge_into_a_detached_host_checkout() -> None:
    git = FakeGitClient()
    git.host_branch = None
    worktree = _worktree(git)

    with pytest.raises(LoopError, match="detached HEAD"):
        with_sandbox_lifecycle(
            git,
            git.commits,
            git.branch_service,
            git.worktree_service,
            _Sandbox(),
            CHECKOUT,
            worktree,
            lambda base_head: None,
            branch=None,
        )


def test_lifecycle_runs_sandbox_ready_hooks_before_the_work() -> None:
    git = FakeGitClient()
    worktree = _worktree(git)
    seen: list[list[str]] = []

    with_sandbox_lifecycle(
        git,
        git.commits,
        git.branch_service,
        git.worktree_service,
        _Sandbox(),
        CHECKOUT,
        worktree,
        lambda base_head: seen.append(list(git.hook_calls)),
        branch="tmp",
        on_sandbox_ready=(Hook("npm install"),),
    )

    assert seen == [["npm install"]]


def test_lifecycle_trusts_the_worktree_and_copies_the_host_identity_into_an_isolated_sandbox() -> None:
    git = FakeGitClient()
    git.config = {"user.name": "Ada L", "user.email": "ada@example.com"}
    sandbox = _Sandbox(isolated=True)
    worktree = _worktree(git)

    with_sandbox_lifecycle(
        git,
        git.commits,
        git.branch_service,
        git.worktree_service,
        sandbox,
        CHECKOUT,
        worktree,
        lambda base_head: None,
        branch="tmp",
    )

    assert sandbox.commands == [
        f"git config --global --get-all safe.directory | grep -qxF {worktree.path}"
        f" || git config --global --add safe.directory {worktree.path}",
        "git config --global user.name 'Ada L'",
        "git config --global user.email ada@example.com",
    ]


def test_lifecycle_leaves_a_host_sandbox_untouched() -> None:
    git = FakeGitClient()
    git.config = {"user.name": "Ada"}
    sandbox = _Sandbox()

    with_sandbox_lifecycle(
        git,
        git.commits,
        git.branch_service,
        git.worktree_service,
        sandbox,
        CHECKOUT,
        _worktree(git),
        lambda base_head: None,
        branch="tmp",
    )

    assert sandbox.commands == []


def test_lifecycle_retries_a_transient_sandbox_setup_exit_then_continues() -> None:
    git = FakeGitClient()
    sandbox = _Sandbox(isolated=True, exits=[137, 0])
    sleeps: list[float] = []

    with_sandbox_lifecycle(
        git,
        git.commits,
        git.branch_service,
        git.worktree_service,
        sandbox,
        CHECKOUT,
        _worktree(git),
        lambda base_head: None,
        branch="tmp",
        sleep=sleeps.append,
    )

    assert len(sandbox.commands) == 2 and sleeps == [0.25]


def test_lifecycle_fails_after_exhausting_retries_on_a_transient_sandbox_setup_exit() -> None:
    git = FakeGitClient()
    sandbox = _Sandbox(isolated=True, exits=[137, 137, 137])
    sleeps: list[float] = []

    with pytest.raises(LoopError, match="137"):
        with_sandbox_lifecycle(
            git,
            git.commits,
            git.branch_service,
            git.worktree_service,
            sandbox,
            CHECKOUT,
            _worktree(git),
            lambda base_head: None,
            branch="tmp",
            sleep=sleeps.append,
        )

    assert sleeps == [0.25, 0.25]


def test_lifecycle_fails_at_once_on_a_non_transient_sandbox_setup_exit() -> None:
    git = FakeGitClient()
    sandbox = _Sandbox(isolated=True, exits=[1])
    sleeps: list[float] = []

    with pytest.raises(LoopError):
        with_sandbox_lifecycle(
            git,
            git.commits,
            git.branch_service,
            git.worktree_service,
            sandbox,
            CHECKOUT,
            _worktree(git),
            lambda base_head: None,
            branch="tmp",
            sleep=sleeps.append,
        )

    assert sleeps == [] and len(sandbox.commands) == 1


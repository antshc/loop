from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from loop import (
    AgentOptions,
    Branch,
    Cancelled,
    Hook,
    HookError,
    InMemorySessionStore,
    LoopError,
    Worktree,
    copilot,
    create_worktree_runner,
    run_lifecycle,
)
from loop.testing import FakeAgentClient, FakeCopilotCli, FakeGit

CHECKOUT = Path("/repo")
HARNESS = Path("/harness")
_RESPONSE = '{"identifier": "t|1", "status": "completed", "result": {}}'


def _event(delta: str) -> str:
    return json.dumps({"type": "assistant.message_delta", "data": {"deltaContent": delta}})


def _runner(git: FakeGit, tmp_path: Path, **kwargs):
    kwargs.setdefault("worktree_root", tmp_path / "workspace" / f"{CHECKOUT.name}.worktrees")
    kwargs.setdefault("executor", FakeCopilotCli())
    return create_worktree_runner(git, checkout=CHECKOUT, harness_root=tmp_path, base="main", **kwargs)


def test_create_runs_worktree_ready_hooks_on_a_generated_branch(tmp_path: Path) -> None:
    git = FakeGit()

    runner = _runner(git, tmp_path, hooks=(Hook("a"), Hook("b")))

    assert git.hook_calls == ["a", "b"]
    assert runner.branch.startswith("loop/run-")
    assert git.worktree_branches == {runner.worktree.path: runner.branch}


def test_create_roots_the_worktree_under_an_explicit_worktree_root(tmp_path: Path) -> None:
    worktree_root = tmp_path / "custom-worktrees"

    runner = _runner(FakeGit(), tmp_path, branch="feature-x", worktree_root=worktree_root)

    assert runner.worktree.path == worktree_root / "feature-x"


def test_create_removes_the_worktree_when_a_hook_fails(tmp_path: Path) -> None:
    git = FakeGit()
    git.failing_hooks.add("a")

    with pytest.raises(HookError):
        _runner(git, tmp_path, branch="feature-x", hooks=(Hook("a"),))

    assert git.worktree_branches == {}


def test_create_removes_a_clean_worktree_when_cancelled_during_a_hook(tmp_path: Path) -> None:
    git = FakeGit()
    git.cancelled_hooks.add("a")

    with pytest.raises(Cancelled) as raised:
        _runner(git, tmp_path, branch="feature-x", hooks=(Hook("a"),))

    assert raised.value.worktree is None
    assert git.worktree_branches == {}


def test_create_keeps_a_dirty_worktree_when_cancelled_during_a_hook(tmp_path: Path) -> None:
    git = FakeGit()
    git.cancelled_hooks.add("a")
    git.branch_commits["feature-x"] = ["c1"]

    with pytest.raises(Cancelled) as raised:
        _runner(git, tmp_path, branch="feature-x", hooks=(Hook("a"),))

    assert raised.value.worktree is not None and raised.value.worktree in git.worktree_branches


def test_run_returns_the_agent_result_and_the_commits_the_agent_made_on_the_branch(tmp_path: Path) -> None:
    git = FakeGit()
    runner = _runner(git, tmp_path, branch="feature-x")
    agent = FakeAgentClient(lambda prompt, options: git.commit(runner.worktree.path, "x") and "done")

    first = runner.run(lambda binding: agent, "go")
    second = runner.run(lambda binding: agent, "go")

    assert first.result.stdout == "done" and first.branch == "feature-x"
    assert first.commits == (f"{1:040d}",) and second.commits == (f"{2:040d}",)
    assert git.merged == [] and git.deleted_branches == []


def test_run_delegates_prompt_args_and_options_and_may_use_a_different_agent_each_time(tmp_path: Path) -> None:
    runner = _runner(FakeGit(), tmp_path, branch="feature-x")
    planner = FakeAgentClient(lambda prompt, options: f"{prompt}:{options.model}")
    reviewer = FakeAgentClient(lambda prompt, options: "reviewed")

    first = runner.run(lambda binding: planner, "p={{A}}", {"A": "1"}, AgentOptions(model="m"))
    second = runner.run(lambda binding: reviewer, "review")

    assert (first.result.stdout, second.result.stdout) == ("p=1:m", "reviewed")


def test_run_binds_the_agent_to_the_harness_root(tmp_path: Path) -> None:
    runner = _runner(FakeGit(), tmp_path, branch="feature-x")
    seen: list[str] = []

    runner.run(lambda binding: seen.append(binding.workspace) or FakeAgentClient(lambda prompt, options: ""), "go")

    assert seen == [str(tmp_path)]


def test_run_executes_commands_on_the_host_in_the_harness_root_by_default(tmp_path: Path) -> None:
    runner = _runner(FakeGit(), tmp_path, branch="feature-x", executor=None)
    outputs: list[str] = []

    runner.run(lambda binding: outputs.append(binding.executor("pwd").stdout.strip()) or FakeAgentClient(), "go")

    assert outputs == [str(tmp_path)]


def test_run_streams_copilot_output_and_parses_the_response_after_exit(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event(_RESPONSE), _event("trailing")])
    runner = _runner(FakeGit(), tmp_path, branch="feature-x", executor=cli)

    result = runner.run(copilot(InMemorySessionStore()), "go").result

    assert result.success and json.loads(result.response)["identifier"] == "t|1"
    assert "trailing" in result.stdout and not cli.terminated


def test_run_cancels_an_in_flight_agent_run_and_reports_cancelled(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event("never")])
    cancel = threading.Event()
    runner = _runner(FakeGit(), tmp_path, branch="feature-x", executor=cli, cancel=cancel)
    cancel.set()

    with pytest.raises(Cancelled):
        runner.run(copilot(InMemorySessionStore()), "go")

    assert cli.terminated


def test_run_with_merge_to_head_merges_each_run_and_keeps_the_branch(tmp_path: Path) -> None:
    git = FakeGit()
    runner = _runner(git, tmp_path, branch="feature-x", merge_to_head=True)
    agent = FakeAgentClient(lambda prompt, options: "done")

    runner.run(lambda binding: agent, "go")
    runner.run(lambda binding: agent, "go")

    assert git.merged == [(CHECKOUT, "feature-x")] * 2
    assert git.detached == set() and git.deleted_branches == []


def test_close_removes_the_worktree_once(tmp_path: Path) -> None:
    git = FakeGit()
    runner = _runner(git, tmp_path, branch="feature-x")

    runner.close()
    runner.close()

    assert git.removed == [runner.worktree.path]


def test_close_keeps_the_worktree_on_request(tmp_path: Path) -> None:
    git = FakeGit()

    _runner(git, tmp_path, branch="feature-x").close(keep_worktree=True)

    assert git.removed == []


def test_leaving_the_context_cancelled_keeps_a_dirty_worktree_and_removes_a_clean_one(tmp_path: Path) -> None:
    git = FakeGit()
    dirty = _runner(git, tmp_path, branch="dirty")
    git.commit(dirty.worktree.path, "x")
    clean = _runner(git, tmp_path, branch="clean")

    dirty.__exit__(Cancelled, Cancelled(), None)
    clean.__exit__(Cancelled, Cancelled(), None)

    assert git.removed == [clean.worktree.path]


def test_leaving_the_context_without_an_error_removes_the_worktree(tmp_path: Path) -> None:
    git = FakeGit()

    with _runner(git, tmp_path, branch="feature-x") as runner:
        pass

    assert git.removed == [runner.worktree.path]


def _worktree(git: FakeGit, branch: str = "tmp") -> Worktree:
    return git.worktrees.create(Branch(CHECKOUT, branch), HARNESS / branch)


def _lifecycle(git: FakeGit, worktree: Worktree, work, **kwargs):
    return run_lifecycle(git.commits, git.branches, git.worktrees, CHECKOUT, worktree, work, **kwargs)


def test_lifecycle_in_temp_branch_mode_merges_into_the_host_branch_then_detaches_and_deletes_the_temp_branch() -> None:
    git = FakeGit()
    worktree = _worktree(git)

    outcome = _lifecycle(git, worktree, lambda base_head: git.commit(worktree.path, "x"), branch=None)

    assert outcome.branch == "tmp" and outcome.commits == (f"{1:040d}",)
    assert git.merged == [(CHECKOUT, "tmp")]
    assert git.detached == {worktree.path} and git.deleted_branches == ["tmp"]


def test_lifecycle_with_keep_source_branch_skips_the_detach_and_delete() -> None:
    git = FakeGit()

    _lifecycle(git, _worktree(git), lambda base_head: None, branch=None, keep_source_branch=True)

    assert git.merged == [(CHECKOUT, "tmp")]
    assert git.detached == set() and git.deleted_branches == []


def test_lifecycle_with_an_explicit_branch_neither_merges_nor_deletes() -> None:
    git = FakeGit()

    _lifecycle(git, _worktree(git), lambda base_head: None, branch="tmp")

    assert git.merged == [] and git.deleted_branches == []


def test_lifecycle_passes_the_head_before_the_work_to_the_work() -> None:
    git = FakeGit()
    worktree = _worktree(git)
    git.commit(worktree.path, "earlier")
    seen: list[str] = []

    outcome = _lifecycle(
        git, worktree, lambda base_head: seen.append(base_head) or git.commit(worktree.path, "x"), branch="tmp"
    )

    assert seen == [f"{1:040d}"] and outcome.commits == (f"{2:040d}",)


def test_lifecycle_rejects_a_temp_branch_merge_into_a_detached_host_checkout() -> None:
    git = FakeGit()
    git.host_branch = None

    with pytest.raises(LoopError, match="detached HEAD"):
        _lifecycle(git, _worktree(git), lambda base_head: None, branch=None)

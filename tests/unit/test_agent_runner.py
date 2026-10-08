from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from loop import (
    AgentClient,
    AgentClientFactory,
    AgentOptions,
    AgentRunner,
    AgentRunnerProvider,
    Cancelled,
    Hook,
    HookError,
    InMemorySessionStore,
    LoopError,
    copilot,
)
from loop.testing import FakeAgentClient, FakeCopilotCli, FakeGit

CHECKOUT = Path("/repo")
_RESPONSE = '{"identifier": "t|1", "status": "completed", "result": {}}'


def _event(delta: str) -> str:
    return json.dumps({"type": "assistant.message_delta", "data": {"deltaContent": delta}})


def _runner(
    git: FakeGit,
    tmp_path: Path,
    agent: AgentClientFactory | AgentClient | None = None,
    *,
    executor=...,
    cancel: threading.Event | None = None,
    **kwargs,
) -> AgentRunner:
    if agent is None:
        agent = FakeAgentClient()
    factory = agent if callable(agent) and not isinstance(agent, AgentClient) else (lambda binding: agent)
    provider = AgentRunnerProvider(
        git, tmp_path, factory, executor=FakeCopilotCli() if executor is ... else executor, cancel=cancel
    )
    kwargs.setdefault("worktree_root", tmp_path / "workspace" / f"{CHECKOUT.name}.worktrees")
    return provider.create(checkout=CHECKOUT, base="main", **kwargs)


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


def test_create_removes_the_worktree_when_the_agent_factory_fails(tmp_path: Path) -> None:
    git = FakeGit()

    def failing(binding):
        raise LoopError("no agent")

    with pytest.raises(LoopError, match="no agent"):
        _runner(git, tmp_path, failing, branch="feature-x")

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
    holder: list[AgentRunner] = []
    agent = FakeAgentClient(lambda prompt, options: git.commit(holder[0].worktree.path, "x") and "done")
    runner = _runner(git, tmp_path, agent, branch="feature-x")
    holder.append(runner)

    first = runner.run("go")
    second = runner.run("go")

    assert first.result.stdout == "done" and first.branch == "feature-x"
    assert first.commits == (f"{1:040d}",) and second.commits == (f"{2:040d}",)
    assert git.merged == [] and git.deleted_branches == []


def test_run_delegates_prompt_args_and_options_to_the_agent(tmp_path: Path) -> None:
    agent = FakeAgentClient(lambda prompt, options: f"{prompt}:{options.model}")
    runner = _runner(FakeGit(), tmp_path, agent, branch="feature-x")

    result = runner.run("p={{A}}", {"A": "1"}, AgentOptions(model="m"))

    assert result.result.stdout == "p=1:m"


def test_run_with_new_session_starts_a_conversation_and_returns_its_id(tmp_path: Path) -> None:
    agent = FakeAgentClient()
    runner = _runner(FakeGit(), tmp_path, agent, branch="feature-x")

    first = runner.run("go", new_session=True)
    second = runner.run("go", new_session=True)
    stateless = runner.run("go")

    assert first.session_id and first.session_id != second.session_id and stateless.session_id is None
    assert [options.session_key for _, options in agent.calls] == [first.session_id, second.session_id, None]


def test_run_with_a_session_id_continues_that_conversation(tmp_path: Path) -> None:
    agent = FakeAgentClient()
    runner = _runner(FakeGit(), tmp_path, agent, branch="feature-x")

    first = runner.run("go", new_session=True)
    second = runner.run("again", session_id=first.session_id)

    assert second.session_id == first.session_id
    assert [options.session_key for _, options in agent.calls] == [first.session_id] * 2


def test_run_binds_the_agent_to_the_harness_root(tmp_path: Path) -> None:
    seen: list[str] = []

    def factory(binding):
        seen.append(binding.workspace)
        return FakeAgentClient()

    _runner(FakeGit(), tmp_path, factory, branch="feature-x")

    assert seen == [str(tmp_path)]


def test_run_executes_commands_on_the_host_in_the_harness_root_by_default(tmp_path: Path) -> None:
    outputs: list[str] = []

    def factory(binding):
        outputs.append(binding.executor("pwd").stdout.strip())
        return FakeAgentClient()

    _runner(FakeGit(), tmp_path, factory, branch="feature-x", executor=None)

    assert outputs == [str(tmp_path)]


def test_run_streams_copilot_output_and_parses_the_response_after_exit(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event(_RESPONSE), _event("trailing")])
    runner = _runner(FakeGit(), tmp_path, copilot(InMemorySessionStore()), branch="feature-x", executor=cli)

    result = runner.run("go").result

    assert result.success and json.loads(result.response)["identifier"] == "t|1"
    assert "trailing" in result.stdout and not cli.terminated


def test_run_cancels_an_in_flight_agent_run_and_reports_cancelled(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event("never")])
    cancel = threading.Event()
    runner = _runner(
        FakeGit(), tmp_path, copilot(InMemorySessionStore()), branch="feature-x", executor=cli, cancel=cancel
    )
    cancel.set()

    with pytest.raises(Cancelled):
        runner.run("go")

    assert cli.terminated


def test_run_with_merge_to_head_merges_each_run_and_keeps_the_branch(tmp_path: Path) -> None:
    git = FakeGit()
    runner = _runner(git, tmp_path, branch="feature-x", merge_to_head=True)

    runner.run("go")
    runner.run("go")

    assert git.merged == [(CHECKOUT, "feature-x")] * 2
    assert git.detached == set() and git.deleted_branches == []


def test_exit_disposes_the_client_then_removes_the_worktree_once(tmp_path: Path) -> None:
    git = FakeGit()
    order: list[str] = []

    class Tracking(FakeAgentClient):
        def exit(self) -> None:
            order.append(f"client:{len(git.removed)}")

    runner = _runner(git, tmp_path, Tracking(), branch="feature-x")

    runner.exit()
    runner.exit()

    assert git.removed == [runner.worktree.path]
    assert order[0] == "client:0"


def test_exit_keeps_the_worktree_on_request(tmp_path: Path) -> None:
    git = FakeGit()

    _runner(git, tmp_path, branch="feature-x").exit(keep_worktree=True)

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


def test_run_with_merge_to_head_rejects_a_detached_host_checkout(tmp_path: Path) -> None:
    git = FakeGit()
    git.host_branch = None
    runner = _runner(git, tmp_path, branch="feature-x", merge_to_head=True)

    with pytest.raises(LoopError, match="detached HEAD"):
        runner.run("go")

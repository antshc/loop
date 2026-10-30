"""Reusable Python agent profiles through the public execution interface."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from http.client import IncompleteRead
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

import pytest

from loop import (
    Agent, AgentBuilder, AgentCliHookWiring, AgentContext, AgentOptions, AgentProfile, AgentRequest,
    AgentStartMode, GitOptions, Instructions, InstructionsError, MemorySessionStore,
    Permissions, ProcessCliRunner, RunContext, SessionName, SessionStartAgentCliHook, Start,
    UnsupportedPermissions, codex, copilot,
)
from loop.docker import DockerRuntime
from test_agent_builder import RecordingDocker, RecordingGit, RecordingRunner


def builder(runner: RecordingRunner, **kwargs: object) -> AgentBuilder:
    return AgentBuilder(runner, RecordingGit([]), RecordingDocker([]), **kwargs)


def test_profile_instructions_are_optional_and_preserve_request_names() -> None:
    """Given an old profile, when run, then its request is unchanged."""
    runner = RecordingRunner([])
    request = AgentRequest("Review cart", "cart-review")
    builder(runner).create(AgentProfile(copilot)).run(request)
    assert runner.requests == [request]


def test_file_definition_is_inert_and_clients_snapshot_instructions(tmp_path: Path) -> None:
    """Given a file profile, when clients bind, then each snapshots the current text."""
    path = tmp_path / "developer.md"
    source = Instructions.from_file(path)
    profile = AgentProfile(copilot, instructions=source)
    path.write_text("Implement and verify.", encoding="utf-8")
    runner = RecordingRunner([])
    client = builder(runner).with_session().create(profile)
    path.write_text("Changed instructions.", encoding="utf-8")
    client.run(AgentRequest("Add cart item"))
    client.run(AgentRequest("Fix quantity"))
    builder(runner).create(profile).run(AgentRequest("Review cart", "review"))
    assert runner.requests[0].prompt == "## Agent instructions\nImplement and verify.\n\n## Task\nAdd cart item"
    assert "Implement and verify." in runner.requests[1].prompt
    assert "Changed instructions." in runner.requests[2].prompt
    assert runner.requests[2].name == "review"


def test_instructions_are_literal_and_request_templates_are_not_expanded() -> None:
    """Given literal placeholders, when run, then composition leaves them unchanged."""
    runner = RecordingRunner([])
    builder(runner).create(AgentProfile(copilot, instructions="Use {{RULE}}.")).run(AgentRequest("{{TASK}}"))
    assert "{{RULE}}" in runner.requests[0].prompt
    assert "{{TASK}}" in runner.requests[0].prompt


def test_missing_file_never_registers_a_session(tmp_path: Path) -> None:
    """Given missing instructions, when binding, then no runner or session is touched."""
    runner, store, session = RecordingRunner([]), MemorySessionStore(), SessionName("cart")
    profile = AgentProfile(copilot, instructions=Instructions.from_file(tmp_path / "missing.md"))
    with pytest.raises(InstructionsError):
        builder(runner, sessions=store).with_session().create(profile, session)
    assert store.get(session, "copilot") is None
    assert not runner.requests


def response(content: bytes, content_type: str = "text/plain") -> BytesIO:
    result = BytesIO(content)
    result.headers = {"Content-Type": content_type}
    return result


def test_url_is_loaded_once_with_bounded_timeout() -> None:
    """Given URL instructions, when a client runs twice, then it fetches only at binding."""
    with patch("loop.run.instructions.urlopen", return_value=response(b"Review cart.")) as fetch:
        profile = AgentProfile(copilot, instructions=Instructions.from_url("https://example.com/reviewer.md"))
        fetch.assert_not_called()
        runner = RecordingRunner([])
        client = builder(runner).create(profile)
        client.run(AgentRequest("First"))
        client.run(AgentRequest("Second"))
        fetch.assert_called_once_with("https://example.com/reviewer.md", timeout=10)
    assert all("Review cart." in request.prompt for request in runner.requests)


@pytest.mark.parametrize("payload,content_type", [(b"", "text/plain"), (b"\xff", "text/plain"), (b"<html>Login</html>", "text/html"), (b"x" * (1024 * 1024 + 1), "text/plain")])
def test_invalid_url_content_is_rejected(payload: bytes, content_type: str) -> None:
    """Given unusable remote content, when binding, then loading fails explicitly."""
    with patch("loop.run.instructions.urlopen", return_value=response(payload, content_type)):
        with pytest.raises(InstructionsError):
            builder(RecordingRunner([])).create(AgentProfile(copilot, instructions=Instructions.from_url("https://example.com/a.md")))


@pytest.mark.parametrize("error", [URLError("offline"), IncompleteRead(b"", 10)])
def test_network_failure_is_reported_as_instruction_error(error: Exception) -> None:
    """Given a failed fetch, when binding, then its cause remains available."""
    with patch("loop.run.instructions.urlopen", side_effect=error):
        with pytest.raises(InstructionsError) as caught:
            builder(RecordingRunner([])).create(AgentProfile(copilot, instructions=Instructions.from_url("https://example.com/a.md")))
    assert caught.value.__cause__ is error


def test_dry_run_does_not_load_remote_or_local_instructions(tmp_path: Path) -> None:
    """Given dry-run profiles, when run, then instruction sources perform no I/O."""
    with patch("loop.run.instructions.urlopen", side_effect=AssertionError("unexpected fetch")):
        for source in (Instructions.from_url("https://example.com/a.md"), Instructions.from_file(tmp_path / "missing.md")):
            result = Agent(AgentOptions(dry_run=True)).create(AgentProfile(copilot, instructions=source)).run(AgentRequest("Review"))
            assert "Review" in result.output


def test_profile_context_is_resolved_before_git_and_docker(tmp_path: Path) -> None:
    """Given profile directories, when wrapped, then Git and Docker see the resolved context."""
    runner = RecordingRunner([])
    profile = AgentProfile(copilot, cwd=tmp_path, add_dirs=(tmp_path / "docs",), env={"APP": "cart"}, timeout_sec=12)
    builder(runner).with_git().with_docker().create(profile).run(AgentRequest("Implement"))
    context = runner.contexts[0].agent
    assert context.cwd == tmp_path / "worktree"
    assert context.add_dirs == (tmp_path / "docs",)
    assert context.env == {"APP": "cart"}
    assert context.timeout_sec == 12
    assert context.docker_image == "test-image"


def test_workspace_keeps_its_directory_over_profile_cwd(tmp_path: Path) -> None:
    """Given an open worktree, when binding a profile, then that worktree wins."""
    runner = RecordingRunner([])
    with builder(runner, agent_context=AgentContext(cwd=tmp_path)).with_git().open() as workspace:
        workspace.agent(AgentProfile(copilot, cwd=tmp_path / "elsewhere", add_dirs=())).run(AgentRequest("Review"))
    assert runner.contexts[0].agent.cwd == tmp_path / "worktree"
    assert runner.contexts[0].agent.add_dirs == ()


def test_explicit_run_context_replaces_profile_defaults(tmp_path: Path) -> None:
    """Given profile defaults, when a context is passed, then it replaces them completely."""
    runner = RecordingRunner([])
    profile = AgentProfile(copilot, cwd=tmp_path, add_dirs=(tmp_path / "docs",), env={"APP": "cart"}, timeout_sec=12)
    override = AgentContext(cwd=tmp_path / "override", env={"APP": "checkout"}, timeout_sec=4)
    builder(runner).create(profile).run(AgentRequest("Review"), override)
    assert runner.contexts[0].agent == override


def test_harness_launch_applies_after_profile_context_resolution(tmp_path: Path) -> None:
    """Given harness mode, when running a profile, then its target directory remains explicit."""
    runner = RecordingRunner([])
    context = AgentContext(start_mode=AgentStartMode.HARNESS, harness_dir=tmp_path / "harness")
    builder(runner, agent_context=context).create(AgentProfile(copilot, cwd=tmp_path / "target")).run(AgentRequest("Review"))
    assert runner.contexts[0].agent.cwd == tmp_path / "harness"
    assert tmp_path / "target" in runner.contexts[0].agent.add_dirs
    assert str(tmp_path / "target") in runner.requests[0].prompt


def test_profile_environment_and_timeout_reach_the_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Given process defaults, when run, then environment merges and timeout are forwarded."""
    monkeypatch.setenv("INHERITED", "yes")
    process = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
    runner = ProcessCliRunner(run=process)
    profile = AgentProfile(copilot, cwd=tmp_path, env={"APP": "cart"}, timeout_sec=30)
    AgentBuilder(runner, RecordingGit([]), DockerRuntime()).create(profile).run(AgentRequest("Review"))
    options = process.call_args.kwargs
    assert options["cwd"] == tmp_path
    assert options["env"]["INHERITED"] == "yes"
    assert options["env"]["APP"] == "cart"
    assert options["timeout"] == 30


def test_hook_wiring_environment_takes_precedence_over_profile_values(tmp_path: Path) -> None:
    """Given native hook wiring, when a profile conflicts, then wiring remains effective."""
    process = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
    profile = AgentProfile(copilot, cwd=tmp_path, env={"HOOK_STATE": "caller", "APP": "cart"})
    wiring = AgentCliHookWiring(env={"HOOK_STATE": "wired"})
    agent = AgentBuilder(ProcessCliRunner(run=process), RecordingGit([]), DockerRuntime())
    with patch.object(copilot, "hook_wiring", return_value=wiring):
        agent.with_agent_cli_hooks(SessionStartAgentCliHook("observe")).create(profile).run(AgentRequest("Review"))
    assert process.call_args.kwargs["env"]["HOOK_STATE"] == "wired"
    assert process.call_args.kwargs["env"]["APP"] == "cart"


def test_process_timeout_is_not_reported_as_an_agent_result(tmp_path: Path) -> None:
    """Given an expired process timeout, when running, then TimeoutExpired propagates."""
    process = Mock(side_effect=subprocess.TimeoutExpired("copilot", 1))
    profile = AgentProfile(copilot, cwd=tmp_path, timeout_sec=1)
    agent = AgentBuilder(ProcessCliRunner(run=process), RecordingGit([]), DockerRuntime())
    with pytest.raises(subprocess.TimeoutExpired):
        agent.create(profile).run(AgentRequest("Review"))


def test_permissions_replace_copilot_broad_defaults_and_emit_deny_rules() -> None:
    """Given explicit permissions, when translating, then broad defaults cannot bypass them."""
    profile = AgentProfile(copilot, permissions=Permissions(tools=("read", "shell(git diff)"), deny_tools=("shell(git push)",)))
    command = copilot.command(AgentRequest("Review"), profile, Start(SessionName("cart")), RunContext())
    assert "--allow-all" not in command
    assert "--allow-all-tools" not in command
    assert command[command.index("--deny-tool") + 1] == "shell(git push)"
    assert command.count("--allow-tool") == 2


def test_allow_all_tools_does_not_implicitly_allow_all_paths_and_urls() -> None:
    """Given tool approval only, when translating, then permissions remain tool-scoped."""
    profile = AgentProfile(copilot, permissions=Permissions(tools="allow-all"))
    command = copilot.command(AgentRequest("Review"), profile, Start(SessionName("cart")), RunContext())
    assert "--allow-all-tools" in command
    assert "--allow-all" not in command


@pytest.mark.parametrize("flag", ["--allow-all", "--yolo", "--allow-tool=write", "--deny-tool", "--allow-all-tools"])
def test_permission_arguments_cannot_conflict_with_typed_policy(flag: str) -> None:
    """Given conflicting raw arguments, when binding, then validation rejects ambiguity."""
    with pytest.raises(ValueError, match="permission"):
        builder(RecordingRunner([])).create(AgentProfile(copilot, permissions=Permissions(), args=(flag,)))


def test_unsupported_permissions_fail_before_session_registration() -> None:
    """Given a CLI without tool policies, when binding, then no session is registered."""
    store, session = MemorySessionStore(), SessionName("review")
    with pytest.raises(UnsupportedPermissions):
        builder(RecordingRunner([]), sessions=store).with_session().create(AgentProfile(codex, permissions=Permissions()), session)
    assert store.get(session, "codex") is None


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected(timeout: float) -> None:
    """Given an invalid timeout, when configuring, then the caller gets a clear error."""
    with pytest.raises(ValueError, match="timeout"):
        AgentProfile(copilot, timeout_sec=timeout)
    with pytest.raises(ValueError, match="timeout"):
        AgentContext(timeout_sec=timeout)


def test_profile_copies_environment_values(tmp_path: Path) -> None:
    """Given mutable input, when it changes, then existing profiles retain their defaults."""
    values = {"APP": "cart"}
    profile = AgentProfile(copilot, env=values)
    values["APP"] = "changed"
    assert profile.env == {"APP": "cart"}
    with pytest.raises(TypeError):
        profile.env["APP"] = "changed"


@pytest.mark.parametrize("source", [lambda: Instructions.from_file(Path("relative.md")), lambda: Instructions.from_url("file:///tmp/prompt.md"), lambda: Instructions.from_url("https://user:password@example.com/a.md")])
def test_invalid_instruction_sources_are_rejected_without_loading(source: Callable[[], Instructions]) -> None:
    """Given an ambiguous or invalid source, when declared, then validation fails."""
    with pytest.raises(ValueError):
        source()


@pytest.mark.parametrize("values", [{"": "value"}, {"A=B": "value"}, {"APP": "bad\0value"}])
def test_environment_validation_prevents_invalid_process_arguments(values: dict[str, str]) -> None:
    """Given invalid environment values, when configured, then subprocess setup cannot fail later."""
    with pytest.raises(ValueError, match="environment"):
        AgentProfile(copilot, env=values)


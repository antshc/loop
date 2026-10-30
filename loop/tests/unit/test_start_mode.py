"""Harness start mode: `pytest tests/unit/test_start_mode.py`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from loop import (
    Agent,
    AgentBuilder,
    AgentContext,
    AgentOptions,
    AgentProfile,
    AgentRequest,
    AgentStartMode,
    CliOutcome,
    CodexCli,
    GitOptions,
    NativeHandle,
    Resume,
    RunContext,
    Turn,
    codex,
    copilot,
)
from loop.docker import DockerRuntime
from loop.run.launch import apply_start_mode, directory_instruction

HARNESS = Path("/harness")
REPO = Path("/repo")


class Runner:
    def __init__(self) -> None:
        self.calls: list[tuple[AgentProfile, AgentRequest, Turn, RunContext]] = []

    def run(self, profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext) -> CliOutcome:
        self.calls.append((profile, request, turn, context))
        handle = turn.handle if isinstance(turn, Resume) else NativeHandle(profile.cli.name, "h")
        return CliOutcome("ok", handle, 0)


class Git:
    @contextmanager
    def open(self, cwd: Path, options: GitOptions) -> Iterator[Path]:
        yield cwd / "worktree"


def harness_context(**overrides: object) -> AgentContext:
    return AgentContext(cwd=REPO, start_mode=AgentStartMode.HARNESS, harness_dir=HARNESS, **overrides)


def test_default_mode_is_worktree_and_leaves_context_and_prompt_untouched() -> None:
    context, request = AgentContext(cwd=REPO), AgentRequest("do it")

    assert context.start_mode is AgentStartMode.WORKTREE
    assert apply_start_mode(context, request) == (context, request)


def test_harness_mode_requires_a_harness_dir() -> None:
    with pytest.raises(ValueError, match="harness_dir"):
        AgentContext(cwd=REPO, start_mode=AgentStartMode.HARNESS)


def test_invalid_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        AgentContext(cwd=REPO, start_mode="elsewhere")  # type: ignore[arg-type]


def test_mode_accepts_its_string_value() -> None:
    assert AgentContext(cwd=REPO, start_mode="worktree").start_mode is AgentStartMode.WORKTREE  # type: ignore[arg-type]


def test_harness_mode_keeps_the_request_name() -> None:
    _, request = apply_start_mode(harness_context(), AgentRequest("do it", "loop(1) Title"))

    assert request.name == "loop(1) Title"


def test_harness_mode_launches_in_the_harness_grants_the_worktree_and_prefixes_the_prompt() -> None:
    context, request = apply_start_mode(harness_context(add_dirs=(Path("/extra"),)), AgentRequest("do it"))

    assert context.cwd == HARNESS
    assert context.add_dirs == (Path("/extra"), REPO)
    assert request.prompt == f"{directory_instruction(REPO)}\n\ndo it"


def test_worktree_already_granted_is_not_duplicated() -> None:
    context, _ = apply_start_mode(harness_context(add_dirs=(REPO,)), AgentRequest("x"))

    assert context.add_dirs == (REPO,)


def test_instruction_names_and_quotes_the_worktree() -> None:
    text = directory_instruction(Path("/a b/it's"))

    assert "/a b/it's" in text
    assert "cd '/a b/it'\"'\"'s'" in text


def test_options_configure_the_builder_context() -> None:
    builder = Agent(AgentOptions(dry_run=True, start_mode=AgentStartMode.HARNESS, harness_dir=HARNESS))

    assert builder._defaults.agent.start_mode is AgentStartMode.HARNESS
    assert builder._defaults.agent.harness_dir == HARNESS


def test_options_without_a_mode_keep_worktree_mode() -> None:
    assert Agent(AgentOptions(dry_run=True))._defaults.agent.start_mode is AgentStartMode.WORKTREE


def builder(runner: Runner, context: AgentContext) -> AgentBuilder:
    return AgentBuilder(runner, Git(), DockerRuntime(), context).with_git()


@pytest.mark.parametrize("profile", [AgentProfile(copilot), AgentProfile(CodexCli()), AgentProfile(codex)])
def test_open_runs_in_the_harness_with_the_worktree_named_in_the_prompt(profile: AgentProfile) -> None:
    runner = Runner()

    with builder(runner, harness_context()).open() as worktree:
        worktree.agent(profile).run(AgentRequest("do it"))

    _, request, _, run_context = runner.calls[0]
    assert worktree.path == REPO / "worktree"
    assert run_context.agent.cwd == HARNESS
    assert worktree.path in run_context.agent.add_dirs
    assert request.prompt == f"{directory_instruction(worktree.path)}\n\ndo it"


def test_create_runs_in_the_harness_with_the_worktree_named_in_the_prompt() -> None:
    runner = Runner()

    builder(runner, harness_context()).create().run(AgentRequest("do it"))

    _, request, _, run_context = runner.calls[0]
    assert run_context.agent.cwd == HARNESS
    assert REPO / "worktree" in run_context.agent.add_dirs
    assert request.prompt.startswith(directory_instruction(REPO / "worktree"))
    assert request.prompt.endswith("\n\ndo it")


def test_resumed_sessions_get_the_instruction_too() -> None:
    runner = Runner()

    with builder(runner, harness_context()).with_session().open() as worktree:
        agent = worktree.agent()
        agent.run(AgentRequest("first"))
        agent.run(AgentRequest("second"))

    assert isinstance(runner.calls[1][2], Resume)
    assert all(call[1].prompt.startswith(directory_instruction(worktree.path)) for call in runner.calls)
    assert runner.calls[1][1].prompt.endswith("\n\nsecond")


def test_worktree_mode_runs_in_the_worktree_with_the_original_prompt() -> None:
    runner = Runner()

    with builder(runner, AgentContext(cwd=REPO)).open() as worktree:
        worktree.agent().run(AgentRequest("do it"))

    _, request, _, run_context = runner.calls[0]
    assert run_context.agent.cwd == worktree.path == REPO / "worktree"
    assert run_context.agent.add_dirs == ()
    assert request.prompt == "do it"


def test_the_prefixed_prompt_reaches_the_cli_command_after_dash_p() -> None:
    runner = Runner()
    with builder(runner, harness_context()).open() as worktree:
        worktree.agent().run(AgentRequest("do it"))
    _, request, turn, run_context = runner.calls[0]

    argv = copilot.command(request, AgentProfile(copilot), turn, run_context)

    assert argv[:2] == ["copilot", "-p"]
    assert argv[2] == request.prompt and "cd " in argv[2]
    assert ["--add-dir", str(worktree.path)] == argv[argv.index("--add-dir"):argv.index("--add-dir") + 2]

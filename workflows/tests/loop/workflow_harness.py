"""Fakes for the plan-and-implement Workflow."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from loop import (
    AgentBuilder,
    AgentContext,
    AgentProfile,
    AgentRequest,
    CliOutcome,
    DockerRuntime,
    GitOptions,
    NativeHandle,
    RunContext,
)

Handler = Callable[[str], str]


class FakeRunner:
    """A `CliRunner` that answers every run with `handler(prompt)`; a handler may raise `CalledProcessError`."""

    def __init__(self, handler: Handler | None = None) -> None:
        self.handler: Handler = handler or (lambda prompt: "")
        self.prompts: list[str] = []
        self.profiles: list[AgentProfile] = []

    def run(
        self,
        profile: AgentProfile,
        request: AgentRequest,
        turn: object,
        context: RunContext,
    ) -> CliOutcome:
        self.prompts.append(request.prompt)
        self.profiles.append(profile)
        output = self.handler(request.prompt)
        return CliOutcome(
            output, NativeHandle(profile.cli.name, f"session{len(self.prompts)}"), 0
        )


class FakeGitService:
    """A `GitService` whose worktree is a plain directory; records the options it was opened with."""

    def __init__(self, worktree: Path) -> None:
        self.worktree = worktree
        self.opened: list[GitOptions] = []
        self.closed = 0
        self.open_error: Exception | None = None
        self.on_open: Callable[[], None] = lambda: None

    @contextmanager
    def open(self, cwd: Path, options: GitOptions) -> Iterator[Path]:
        self.opened.append(options)
        self.on_open()
        if self.open_error is not None:
            raise self.open_error
        self.worktree.mkdir(parents=True, exist_ok=True)
        try:
            yield self.worktree
        finally:
            self.closed += 1


def new_agent_for(
    runner: FakeRunner, git_service: FakeGitService, cwd: Path
) -> Callable[[], AgentBuilder]:
    return lambda: AgentBuilder(
        runner, git_service, DockerRuntime(), AgentContext(cwd=cwd)
    )


def crash(output: str = "") -> Handler:
    def handler(prompt: str) -> str:
        raise subprocess.CalledProcessError(1, "copilot", output, "crashed")

    return handler


def plan_envelope(
    status: str = "completed", plan: str = "do the work", reason: str = "stuck"
) -> str:
    tag = "Success" if status == "completed" else f"Failure: {reason}"
    body = plan if status == "completed" else ""
    return f"{body}\n<completed>{tag}</completed>"


def implement_envelope(status: str = "completed", reason: str = "stuck") -> str:
    tag = "Success" if status == "completed" else f"Failure: {reason}"
    return f"<completed>{tag}</completed>"

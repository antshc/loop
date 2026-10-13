"""Fakes for the plan-and-implement Workflow."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from loop import (
    AgentBuilder, AgentContext, AgentProfile, AgentRequest, CliOutcome, DockerRuntime,
    GitOptions, NativeHandle, RunContext,
)
from workflows.platforms.git import Commit


Handler = Callable[[str], str]


class FakeRunner:
    """A `CliRunner` that answers every run with `handler(prompt)`; a handler may raise `CalledProcessError`."""

    def __init__(self, handler: Handler | None = None) -> None:
        self.handler: Handler = handler or (lambda prompt: "")
        self.prompts: list[str] = []
        self.profiles: list[AgentProfile] = []

    def run(self, profile: AgentProfile, request: AgentRequest, turn: object, context: RunContext) -> CliOutcome:
        self.prompts.append(request.prompt)
        self.profiles.append(profile)
        output = self.handler(request.prompt)
        return CliOutcome(output, NativeHandle(profile.cli.name, f"session{len(self.prompts)}"), 0)


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


class FakeWorkflowGit:
    """An in-memory `GitService` over one branch: a commit list, how much of it origin already holds, a dirty flag."""

    def __init__(self) -> None:
        self.commits = [Commit("base0000", "base")]
        self.pushed_count = 1
        self.remote_branches = {"main"}
        self.dirty = False
        self.fetched: list[Path] = []
        self.pushes: list[tuple[Path, str]] = []
        self.restores: list[str] = []

    def commit(self, subject: str) -> str:
        sha = f"c{len(self.commits):07d}"
        self.commits.append(Commit(sha, subject))
        return sha

    def fetch(self, path: Path) -> None:
        self.fetched.append(path)

    def remote_branch_exists(self, path: Path, branch: str) -> bool:
        return branch in self.remote_branches

    def push_if_ahead(self, path: Path, branch: str, base: str) -> bool:
        if len(self.commits) <= self.pushed_count:
            return False
        self.pushes.append((path, branch))
        self.pushed_count = len(self.commits)
        return True

    def head(self, path: Path) -> Commit:
        return self.commits[-1]

    def commits_since(self, path: Path, sha: str) -> list[Commit]:
        index = next(index for index, commit in enumerate(self.commits) if commit.sha == sha)
        return self.commits[index + 1 :]

    def initiative_commits(self, path: Path, base: str, prefix: str) -> list[Commit]:
        return [commit for commit in self.commits[1:] if commit.subject.startswith(prefix)]

    def restore(self, path: Path, sha: str) -> None:
        index = next(index for index, commit in enumerate(self.commits) if commit.sha == sha)
        del self.commits[index + 1 :]
        self.dirty = False
        self.restores.append(sha)

    def is_clean(self, path: Path) -> bool:
        return not self.dirty


def new_agent_for(runner: FakeRunner, git_service: FakeGitService, cwd: Path) -> Callable[[], AgentBuilder]:
    return lambda: AgentBuilder(runner, git_service, DockerRuntime(), AgentContext(cwd=cwd))


def crash(output: str = "") -> Handler:
    def handler(prompt: str) -> str:
        raise subprocess.CalledProcessError(1, "copilot", output, "crashed")

    return handler


def plan_envelope(status: str = "completed", plan: str = "do the work", reason: str = "stuck") -> str:
    tag = "Success" if status == "completed" else f"Failure: {reason}"
    body = plan if status == "completed" else ""
    return f"{body}\n<completed>{tag}</completed>"


def implement_envelope(status: str = "completed", reason: str = "stuck") -> str:
    tag = "Success" if status == "completed" else f"Failure: {reason}"
    return f"<completed>{tag}</completed>"

"""A real temporary Git repository, scripted role agents and a rig that runs a Workflow against them with a temporary ledger."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loop import DEFAULT, AgentContext, AgentRequest, AgentResult, AgentWorkspace, RunContext, SessionName

from loop import Declined, Message, TypedAgent
from loop import (
    Ledger, RecordingSpans, RunResult, Services, StopSignal, Workflow, WorkspaceOpener, WorktreeGit, run,
)

from .support import NoInput


@dataclass(frozen=True)
class Ask(Message):
    """The request every scripted agent receives."""

    text: str


@dataclass(frozen=True)
class Done(Message):
    """The reply of a scripted agent that finished."""

    note: str


@dataclass(frozen=True)
class WorkCtx:
    """The Workflow context: a work item and its revision."""

    id: str = "item-1"
    revision: str = "r1"


def _decode(request: Ask, output: str) -> Done | Declined:
    return Declined(output.removeprefix("decline:").strip()) if output.startswith("decline:") else Done(output.strip())


TESTER: TypedAgent[Ask, Done | Declined] = TypedAgent("tester", DEFAULT, lambda request: request.text, _decode)


def command(args: Sequence[str], path: Path, *, env: Mapping[str, str] | None = None) -> str:
    """Runs `git <args>` in `path` and returns stdout."""
    environment = {**os.environ, **env} if env else None
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True, env=environment).stdout


GIT = WorktreeGit(command)


class Repo:
    """An isolated Git repository with one initial commit."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        command(("init", "-q", "-b", "main"), path)
        for key, value in (("user.name", "Tester"), ("user.email", "tester@example.com"), ("commit.gpgsign", "false")):
            command(("config", key, value), path)
        self.write("README.md", "start\n")
        self.commit("initial")

    def write(self, name: str, content: str) -> None:
        """Writes a file without committing it."""
        (self.path / name).write_text(content)

    def commit(self, subject: str) -> str:
        """Commits everything and returns the new head."""
        command(("add", "-A"), self.path)
        command(("commit", "-q", "-m", subject), self.path)
        return self.head()

    def head(self) -> str:
        """The branch head."""
        return command(("rev-parse", "HEAD"), self.path).strip()

    def subjects(self) -> list[str]:
        """Commit subjects, newest first."""
        return command(("log", "--format=%s"), self.path).splitlines()

    def status(self) -> str:
        """The porcelain status."""
        return command(("status", "--porcelain", "--untracked-files=all"), self.path)


@dataclass
class Script:
    """Answers each agent run with the next action's output; an action may edit or commit in the repository."""

    repo: Repo
    actions: list[Callable[[Repo], str]]
    calls: int = 0

    def run(self, request: AgentRequest) -> AgentResult:
        action = self.actions[min(self.calls, len(self.actions) - 1)]
        self.calls += 1
        return AgentResult(action(self.repo), SessionName.new(), 0)


class _Client:
    def __init__(self, script: Script) -> None:
        self._script = script

    def run(self, request: AgentRequest) -> AgentResult:
        return self._script.run(request)


def committing(subject: str, name: str = "work.txt") -> Callable[[Repo], str]:
    """An agent action that writes a file and commits it with `subject`."""
    def act(repo: Repo) -> str:
        repo.write(name, f"{subject}\n")
        repo.commit(subject)
        return "done"
    return act


def editing(name: str = "work.txt") -> Callable[[Repo], str]:
    """An agent action that edits the working tree and leaves it uncommitted."""
    def act(repo: Repo) -> str:
        repo.write(name, "edited\n")
        return "done"
    return act


def idle() -> Callable[[Repo], str]:
    """An agent action that changes nothing."""
    return lambda repo: "done"


def declining(reason: str = "cannot") -> Callable[[Repo], str]:
    """An agent action that edits the working tree and then declines."""
    def act(repo: Repo) -> str:
        repo.write("work.txt", "half\n")
        return f"decline: {reason}"
    return act


class Rig:
    """A repository, a scripted agent, a temporary ledger and recording spans for one test."""

    def __init__(self, tmp_path: Path) -> None:
        self.repo = Repo(tmp_path / "repo")
        self.ledger = Ledger(tmp_path / "ledger")
        self.spans = RecordingSpans()
        self.script = Script(self.repo, [idle()])

    def agent_does(self, *actions: Callable[[Repo], str]) -> Rig:
        """Sets what each successive agent run does; the last action repeats."""
        self.script = Script(self.repo, list(actions))
        return self

    def checkout(self) -> WorkspaceOpener[Any]:
        """A workspace opener over the repository, with the scripted agent in it."""
        workspace = AgentWorkspace(self.repo.path, lambda *_: _Client(self.script), RunContext(AgentContext()), None)
        return WorkspaceOpener("checkout", NoInput, lambda _: nullcontext(workspace))

    def run(
        self, workflow: Workflow[Any], *services: object, stop: StopSignal | None = None, context: WorkCtx | None = None,
        git: WorktreeGit = GIT,
    ) -> RunResult:
        """Runs the Workflow with Git and the given services."""
        return run(
            workflow, context or WorkCtx(), services=Services(git, *services), ledger=self.ledger, spans=self.spans,
            stop=stop,
        )

    def records(self, workflow: str, item: str = "item-1") -> list[dict[str, Any]]:
        """Every ledger record of a work item as written to disk."""
        path = self.ledger.item(workflow, item).path
        return [json.loads(line) for line in path.read_text().splitlines()]


@dataclass
class StopAfter:
    """A stop signal that turns on once `limit` checks have been answered."""

    limit: int
    checks: int = field(default=0)

    def requested(self) -> bool:
        self.checks += 1
        return self.checks > self.limit

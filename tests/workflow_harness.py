"""Shared Workflow test-support: fakes at the loop library seams and the workflow Git/GitHub boundaries.

`AgentBuilder` is built over a fake `CliRunner` and a fake git service, and `WorkflowGit` is an in-memory fake,
so no test starts a process. Holds no tests of its own.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator, Sequence
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
    LoopHookError,
    LoopHookPoint,
    NativeHandle,
    RunContext,
)
from workflows import dev
from workflows.dev import FileExecutionStore
from workflows.platforms.git import Commit
from workflows.platforms.work_tracking import GitHubClient, RepositoryConfig
from workflows.platforms.work_tracking.fake_gh_cli import FakeGhCli

Handler = Callable[[str], str]


def envelope(identifier: str = "Checkout|10", status: str = "completed", result: dict | None = None) -> str:
    return json.dumps({"identifier": identifier, "status": status, "result": result if result is not None else {}})


COMPLETED = {"commit": "abc", "summary": "done", "verification": "ran tests"}


def issue(number: int, title: str, *, state: str = "OPEN", labels: tuple[str, ...] = ()) -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/issues/{number}",
        "state": state,
        "body": f"Body of #{number}",
        "labels": {"nodes": [{"name": label} for label in labels]},
        "comments": {"nodes": []},
    }


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
    """An in-memory `WorkflowGit` over one branch: a commit list, how much of it origin already holds, a dirty flag."""

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


def hook_error() -> LoopHookError:
    return LoopHookError(LoopHookPoint.WORKTREE_READY, "setup.sh", "exit 1")


class DevHarness:
    """Wires `dev.main` to fakes: a fake CLI runner, git service, workflow git and `gh`, and a real execution store."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        specs: list[dict] | None = None,
        tickets: dict[int, list[dict]] | None = None,
        prs: list[dict] | None = None,
    ) -> None:
        self.tmp_path = tmp_path
        self.root = tmp_path / "harness"
        self.log_dir = tmp_path / "logs"
        self.worktree = tmp_path / "worktree"
        self.git = FakeWorkflowGit()
        self.runner = FakeRunner()
        self.git_service = FakeGitService(self.worktree)
        self.gh = FakeGhCli(
            specs=specs
            if specs is not None
            else [issue(1, "Checkout: Add login page", labels=("spec", "repo:target:owner/repo", "repo:base:main"))],
            tickets=tickets if tickets is not None else {1: [issue(10, "Add login form")]},
            prs=prs,
        )
        self.github = GitHubClient("owner", "repo", gh=self.gh)
        self.store = FileExecutionStore(self.log_dir)

    @property
    def prompts(self) -> list[str]:
        return self.runner.prompts

    def commit_and_report(
        self, number: int, *, initiative: str = "Checkout", summary: str = "done", subject: str | None = None
    ) -> str:
        sha = self.git.commit(subject or f"ccode({initiative}|{number}): work")
        return envelope(f"{initiative}|{number}", result={**COMPLETED, "commit": sha, "summary": summary})

    def commit_then(self, response: Callable[[str], str]) -> Handler:
        return lambda prompt: response(self.git.commit("ccode(Checkout|10): work"))

    def writes(self) -> list[tuple[str, ...]]:
        return [call for call in self.gh.calls if call[0] in ("issue", "pr")]

    def calls(self, *prefix: str) -> list[tuple[str, ...]]:
        return [call for call in self.gh.calls if call[: len(prefix)] == prefix]

    def run(
        self,
        handler: Handler | None = None,
        *,
        github_factory: Callable[[Path], object] | None = None,
        repositories: Sequence[RepositoryConfig] | None = None,
        store: FileExecutionStore | None = None,
        argv: list[str] | None = None,
    ) -> int:
        if handler is not None:
            self.runner.handler = handler
        return dev.main(
            argv if argv is not None else ["--log-dir", str(self.log_dir)],
            git=self.git,  # type: ignore[arg-type]
            github_factory=github_factory or (lambda checkout: self.github),  # type: ignore[arg-type,return-value]
            repositories=repositories
            if repositories is not None
            else [RepositoryConfig(path=self.root, owner_repo="owner/repo", is_harness=True)],
            new_agent=new_agent_for(self.runner, self.git_service, self.root),
            store=store or self.store,
        )


def crash(output: str = "") -> Handler:
    def handler(prompt: str) -> str:
        raise subprocess.CalledProcessError(1, "copilot", output, "crashed")

    return handler


def plan_envelope(status: str = "completed", plan: str = "do the work", reason: str = "stuck") -> str:
    result = {"plan": plan} if status == "completed" else {"reason": reason}
    return json.dumps({"status": status, "result": result})


def implement_envelope(status: str = "completed", reason: str = "stuck") -> str:
    result = {} if status == "completed" else {"reason": reason}
    return json.dumps({"status": status, "result": result})

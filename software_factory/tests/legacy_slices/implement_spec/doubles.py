"""Workflow-owned harness and fakes for tests at the Workflow's injected seams; the `gh` fake is shared."""

from __future__ import annotations

import json
import re
import subprocess
from functools import partial
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from loop import (
    AgentBuilder, AgentContext, AgentProfile, AgentRequest, CliOutcome, DockerRuntime,
    GitOptions, LoopHookError, LoopHookPoint, NativeHandle, RunContext,
)
from software_factory.legacy_slices.implement_spec.agents.delegates import implement_ticket
from software_factory.legacy_slices.implement_spec.agents.prompts import load_prompt
from software_factory.legacy_slices.implement_spec.composition.entry import main
from software_factory.legacy_slices.implement_spec.composition.repositories import configured_repositories
from software_factory.legacy_slices.implement_spec.deliver.journal import FileExecutionStore
from software_factory.legacy_slices.implement_spec.model.records import AgentCommitSubject, Commit, Repository
from software_factory.legacy_slices.implement_spec.model.repositories import GithubFactory, Harness, RepositoryWorkflow, Targets
from software_factory.shared.agents.launcher import AgentFactory
from software_factory.shared.git import GitClient
from software_factory.shared.github.identity import RepositoryId
from software_factory.shared.github.issues.client import Issues
from software_factory.shared.github.repo import GitHubRepo
from software_factory.shared.process import CommandRunner
from software_factory.shared.providers import bind
from software_factory.shared.telemetry import ObserverFactory
from software_factory.tests.shared.fakes.gh import FakeGhCli, issue


Handler = Callable[[str], str]


def envelope(identifier: str = "Checkout|10", status: str = "completed", result: dict | None = None) -> str:
    return json.dumps({"identifier": identifier, "status": status, "result": result if result is not None else {}})


COMPLETED = {"commit": "abc", "summary": "done", "verification": "ran tests"}


class FakeRunner:
    """A `CliRunner` that answers every run with `handler(prompt)`; a handler may raise `CalledProcessError`."""

    def __init__(self, handler: Handler | None = None) -> None:
        self.handler: Handler = handler or (lambda prompt: "")
        self.prompts: list[str] = []
        self.profiles: list[AgentProfile] = []
        self.plan_prompts: list[str] = []
        self.chore_prompts: list[str] = []
        self.review_prompts: list[str] = []
        self.plan_handler = lambda prompt: envelope("", "failed", {"reason": "needs a product decision"})
        self.chore_handler = lambda prompt: envelope("", result={"summary": "No cleanup needed"})
        self.review_handler = lambda prompt: clean_review(re.search(r"head is commit\s+`([^`]+)`", prompt)[1])

    def run(self, profile: AgentProfile, request: AgentRequest, turn: object, context: RunContext) -> CliOutcome:
        self.profiles.append(profile)
        stage = {"planner": "plan", "chore": "chore", "reviewer": "review"}.get(profile.name)
        if stage is None:
            self.prompts.append(request.prompt)
            output = self.handler(request.prompt)
        else:
            getattr(self, f"{stage}_prompts").append(request.prompt)
            output = getattr(self, f"{stage}_handler")(request.prompt)
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
        self._changed: list[str] = []
        self.fetched: list[Path] = []
        self.pushes: list[tuple[Path, str]] = []
        self.restores: list[str] = []

    def commit(self, subject: str | Path, message: str | None = None, body: str = ""):
        sha = f"c{len(self.commits):07d}"
        commit = Commit(sha, message if message is not None else str(subject), body)
        self.commits.append(commit)
        self.dirty = False
        self._changed.clear()
        return commit if message is not None else sha

    def edit(self, path: str = "src/Basket.cs") -> None:
        self.dirty = True
        self._changed.append(path)

    def add_commit(self, subject: str) -> str:
        return self.commit(subject)

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

    def branch_commits(self, path: Path, base: str) -> list[Commit]:
        return self.commits[1:]

    def status(self, path: Path) -> str:
        return "\n".join(f" M {path}" for path in self.changed_files(path))

    def ref_sha(self, path: Path, ref: str) -> str:
        return self.commits[self.pushed_count - 1].sha

    def changed_files(self, path: Path) -> list[str]:
        return list(self._changed) or (["src/Basket.cs"] if self.dirty else [])

    def initiative_commits(self, path: Path, base: str, prefix: str) -> list[Commit]:
        return [commit for commit in self.commits[1:] if commit.subject.startswith(prefix)]

    def restore(self, path: Path, sha: str) -> None:
        index = next(index for index, commit in enumerate(self.commits) if commit.sha == sha)
        del self.commits[index + 1 :]
        self.dirty = False
        self._changed.clear()
        self.restores.append(sha)

    def is_clean(self, path: Path) -> bool:
        return not self.dirty


class FakeRepositoryGit(GitClient):
    """Routes Git calls to isolated fake histories and rejects unexpected checkout or worktree paths."""

    def __init__(self, paths: dict[Path, FakeWorkflowGit]) -> None:
        super().__init__(CommandRunner())
        self.paths = paths

    def fetch(self, path: Path) -> None:
        self.paths[path].fetch(path)

    def remote_branch_exists(self, path: Path, branch: str) -> bool:
        return self.paths[path].remote_branch_exists(path, branch)

    def push_if_ahead(self, path: Path, branch: str, base: str) -> bool:
        return self.paths[path].push_if_ahead(path, branch, base)

    def head(self, path: Path) -> Commit:
        return self.paths[path].head(path)

    def commits_since(self, path: Path, sha: str) -> list[Commit]:
        return self.paths[path].commits_since(path, sha)

    def branch_commits(self, path: Path, base: str) -> list[Commit]:
        return self.paths[path].branch_commits(path, base)

    def initiative_commits(self, path: Path, base: str, prefix: str) -> list[Commit]:
        return self.paths[path].initiative_commits(path, base, prefix)

    def restore(self, path: Path, sha: str) -> None:
        self.paths[path].restore(path, sha)

    def is_clean(self, path: Path) -> bool:
        return self.paths[path].is_clean(path)


class FakeRepositoryGh(FakeGhCli):
    """Returns the target repository's URL when a draft PR is created."""

    def __init__(self, repository: RepositoryId) -> None:
        super().__init__(specs=[], tickets={}, prs=[])
        self.repository = repository

    def __call__(self, args: tuple[str, ...]) -> str:
        output = super().__call__(args)
        if args[:2] == ("pr", "create"):
            return f"https://github.com/{self.repository}/pull/99"
        return output


def new_agent_for(runner: FakeRunner, git_service: FakeGitService, cwd: Path) -> Callable[[], AgentBuilder]:
    return lambda: AgentBuilder(runner, git_service, DockerRuntime(), AgentContext(cwd=cwd))


def hook_error() -> LoopHookError:
    return LoopHookError(LoopHookPoint.WORKTREE_READY, "setup.sh", "exit 1")


class ImplementSpecHarness:
    """Wires `implement_spec.main` to fakes: a fake CLI runner, git service, workflow git and `gh`, and a real execution store."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        specs: list[dict] | None = None,
        tickets: dict[int, list[dict]] | None = None,
        prs: list[dict] | None = None,
        blocked_by: dict[int, list[int]] | None = None,
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
            blocked_by=blocked_by,
        )
        self.github = GitHubRepo("owner", "repo", gh=self.gh)
        self.tracker = Issues(self.github)
        self.store = FileExecutionStore(self.log_dir)

    @property
    def prompts(self) -> list[str]:
        return self.runner.prompts

    def commit_and_report(
        self, number: int, *, initiative: str = "Checkout", summary: str = "done", subject: str | None = None
    ) -> str:
        identifier = AgentCommitSubject(initiative, number)
        sha = self.git.commit(subject or f"{identifier.to_subject()}work")
        return envelope(str(identifier), result={**COMPLETED, "commit": sha, "summary": summary})

    def commit_then(self, response: Callable[[str], str]) -> Handler:
        return lambda prompt: response(self.git.commit(f"{AgentCommitSubject('Checkout', 10).to_subject()}work"))

    def writes(self) -> list[tuple[str, ...]]:
        return [call for call in self.gh.calls if call[0] in ("issue", "pr") or call[:2] == ("api", "--method")]

    def calls(self, *prefix: str) -> list[tuple[str, ...]]:
        return [call for call in self.gh.calls if call[: len(prefix)] == prefix]

    def comments(self, number: int | None = None) -> list[str]:
        """The body of every Comment posted, on every issue or only on issue `number`."""
        return [body for target, body in self.gh.posted_comments() if number in (None, target)]

    def run(
        self,
        handler: Handler | None = None,
        *,
        github_factory: Callable[[Path], object] | None = None,
        store: FileExecutionStore | None = None,
        argv: list[str] | None = None,
        new_observer: Callable[[str], object] | None = None,
        all_phases: bool = False,
        repositories: Sequence[object] | None = None,
    ) -> int:
        if handler is not None:
            self.runner.handler = handler
        harness = Repository(
            checkout=self.root,
            worktrees=self.root / "workspace" / f"{self.root.name}.worktrees",
            owner_name=RepositoryId("owner", "repo"),
        )
        def targets() -> Targets:
            # Resolved inside `main`, so a missing prompt fails the run instead of the test setup.
            if repositories is not None:
                return Targets(repositories)  # type: ignore[arg-type]
            if all_phases:
                return configured_repositories(harness)
            return Targets((RepositoryWorkflow(harness, partial(implement_ticket, template=load_prompt("implement"))),))

        by_checkout = github_factory or (lambda checkout: self.github)
        bindings: list[tuple[object, object]] = [
            (Harness, Harness(harness)),
            (GitClient, self.git),
            (GithubFactory, lambda repository: by_checkout(repository.checkout)),
            (AgentFactory, new_agent_for(self.runner, self.git_service, self.root)),
            (FileExecutionStore, store or self.store),
        ]
        if new_observer is not None:
            bindings.append((ObserverFactory, new_observer))
        overrides = bind(*bindings)
        overrides.provide(targets, provides=Targets, override=True)
        return main(argv if argv is not None else ["--log-dir", str(self.log_dir)], providers=(overrides,))


def crash(output: str = "") -> Handler:
    def handler(prompt: str) -> str:
        raise subprocess.CalledProcessError(1, "copilot", output, "crashed")

    return handler


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit_file(directory: Path, name: str, content: str, message: str) -> None:
    (directory / name).write_text(content)
    git(directory, "add", "-A")
    git(directory, "commit", "-m", message)


class FakePhaseSpan:
    """Accepts workflow span facts without requiring OpenTelemetry in workflow tests."""

    def __init__(self) -> None:
        self.attributes = {}
        self.events = []
        self.status = None

    def set_attribute(self, name, value) -> None:
        self.attributes[name] = value

    def add_event(self, name, attributes) -> None:
        self.events.append((name, attributes))

    def set_status(self, status) -> None:
        self.status = status


def clean_review(head: str) -> str:
    return envelope("", result={
        "axes": [{"name": name, "outcome": "completed"}
                 for name in ("bugs", "quality", "smells", "initiative")],
        "head": head, "findings": [],
    })

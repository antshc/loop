"""Dev-owned fakes and harness for tests at the Workflow's injected seams."""

from __future__ import annotations

import json
import re
import subprocess
from functools import partial
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from loop import (
    AgentBuilder, AgentContext, AgentProfile, AgentRequest, CliOutcome, DockerRuntime,
    GitOptions, LoopHookError, LoopHookPoint, NativeHandle, RunContext,
)
from workflows.dev import run as dev
from workflows.dev.checkout import GitService
from workflows.dev.journal import FileExecutionStore
from workflows.dev.platforms import GitHubRepo
from workflows.dev.records import AgentCommitSubject, Commit, Repository, RepositoryId


class FakeGhCli:
    """Stands in for the `gh` binary: returns canned JSON for reads and records every call."""

    def __init__(
        self,
        *,
        tickets: dict[int, list[dict]] | None = None,
        specs: list[dict] | None = None,
        prs: list[dict] | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._created: dict[int, dict] = {}
        self._tickets = (
            tickets
            if tickets is not None
            else {
                1: [
                    _ticket(10, "Add login form", labels=[]),
                    _ticket(11, "Add login tests", labels=["hitl"]),
                    _ticket(12, "Split login spec", labels=["spec"]),
                    _ticket(13, "Old login task", state="CLOSED", labels=[]),
                ],
            }
        )
        self._specs = (
            specs
            if specs is not None
            else [
                _ticket(1, "Add login page", labels=["spec"]),
                _ticket(2, "Add logout button", labels=["spec"]),
            ]
        )
        self._prs = prs if prs is not None else [_pr(10, "feature/login")]

    def __call__(self, args: tuple[str, ...]) -> str:
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return self._pr_list(args)
        if args[:2] == ("pr", "create"):
            self._prs.append({**_pr(99, args[args.index("--head") + 1]), "body": args[args.index("--body") + 1]})
            return "https://github.com/owner/repo/pull/99\n"
        if args[:2] == ("pr", "edit"):
            for pr in self._prs:
                if str(pr["number"]) == args[2]:
                    pr["body"] = args[args.index("--body") + 1]
            return ""
        if args[:2] == ("pr", "diff"):
            return PR_DIFF
        if args[:2] == ("issue", "create"):
            number = 101 + len(self._created)
            node = issue(number, args[args.index("--title") + 1])
            node["body"] = args[args.index("--body") + 1]
            self._created[number] = node
            return node["url"]
        if args[:2] == ("api", "--method"):
            number = int(args[3].split("/")[-2])
            child = int(args[-1].split("=")[-1]) - 1000
            self._tickets.setdefault(number, []).append(self._created[child])
            return "{}"
        if args and args[0] == "api" and "--jq" in args:
            return str(1000 + int(args[1].split("/")[-1]))
        if args and args[0] == "issue":
            return ""
        query = next((arg for arg in args if arg.startswith("query=")), "")
        if "subIssues" in query:
            number = int(next(arg for arg in args if arg.startswith("number="))[len("number=") :])
            nodes = self._tickets.get(number, [])
            return json.dumps([{"data": {"repository": {"issue": {"subIssues": {"nodes": nodes}}}}}])
        if "labels: [" in query:
            return json.dumps([{"data": {"repository": {"issues": {"nodes": self._specs}}}}])
        return "{}"

    def _pr_list(self, args: tuple[str, ...]) -> str:
        if "--head" in args:
            head = args[args.index("--head") + 1]
            return json.dumps([pr for pr in self._prs if pr["headRefName"] == head])
        return json.dumps(self._prs)


def _ticket(number: int, title: str, *, state: str = "OPEN", labels: list[str] | None = None) -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/issues/{number}",
        "state": state,
        "body": f"Body of #{number}",
        "labels": {"nodes": [{"name": label} for label in labels or []]},
        "comments": {
            "nodes": [{"author": {"login": "alice"}, "body": f"Comment on #{number}", "createdAt": "2026-01-01T00:00:00Z"}]
        },
    }


def _pr(number: int, branch: str, title: str = "Add login page (PR)") -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/pull/{number}",
        "headRefName": branch,
    }


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
        self.plan_prompts: list[str] = []
        self.verify_prompts: list[str] = []
        self.chore_prompts: list[str] = []
        self.review_prompts: list[str] = []
        self.plan_handler = lambda prompt: envelope("", "failed", {"reason": "needs a product decision"})
        self.verify_handler = lambda prompt: envelope("", result={"verdict": "inconclusive"})
        self.chore_handler = lambda prompt: envelope("", result={"summary": "No cleanup needed"})
        self.review_handler = lambda prompt: clean_review(re.search(r"head is commit\s+`([^`]+)`", prompt)[1])

    def run(self, profile: AgentProfile, request: AgentRequest, turn: object, context: RunContext) -> CliOutcome:
        self.profiles.append(profile)
        stage = {"planner": "plan", "verifier": "verify", "chore": "chore", "reviewer": "review"}.get(profile.name)
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


class FakeRepositoryGit(GitService):
    """Routes Git calls to isolated fake histories and rejects unexpected checkout or worktree paths."""

    def __init__(self, paths: dict[Path, FakeWorkflowGit]) -> None:
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
        self.github = GitHubRepo("owner", "repo", gh=self.gh)
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
        return [call for call in self.gh.calls if call[0] in ("issue", "pr")]

    def calls(self, *prefix: str) -> list[tuple[str, ...]]:
        return [call for call in self.gh.calls if call[: len(prefix)] == prefix]

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
        extra = {"new_observer": new_observer} if new_observer is not None else {}
        configured = dev.configured_repositories
        def repository_slice(repository):
            if all_phases:
                return configured(repository)
            from workflows.dev.agent import implement_ticket, load_prompt
            from workflows.dev.repositories import RepositoryWorkflow
            return (RepositoryWorkflow(repository, partial(implement_ticket, template=load_prompt("implement"))),)
        with patch.object(dev, "HARNESS", harness), patch.object(dev, "configured_repositories", repository_slice):
            return dev.main(
                argv if argv is not None else ["--log-dir", str(self.log_dir)],
                git=self.git,  # type: ignore[arg-type]
                github_factory=github_factory or (lambda checkout: self.github),  # type: ignore[arg-type,return-value]
                new_agent=new_agent_for(self.runner, self.git_service, self.root),
                store=store or self.store,
                repositories=repositories,
                **extra,  # type: ignore[arg-type]
            )


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


PR_DIFF = "diff --git a/src/Basket.cs b/src/Basket.cs\n--- a/src/Basket.cs\n+++ b/src/Basket.cs\n@@ -1 +1 @@\n-old\n+new\n"


def clean_review(head: str) -> str:
    return envelope("", result={
        "axes": [{"name": name, "outcome": "completed"}
                 for name in ("bugs", "quality", "smells", "initiative")],
        "head": head, "findings": [],
    })

"""Standalone contract prototype: Agent builder with composed execution wrappers.

No real Git worktree, Docker container, or Copilot process is started here.
"""

from __future__ import annotations

import secrets
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterator, Protocol


@dataclass(frozen=True)
class AgentContext:
    """Agent-level settings; `cli_args` map straight onto the Copilot CLI."""

    cwd: Path = field(default_factory=Path.cwd)
    docker_image: str | None = None
    cli_args: tuple[str, ...] = ("--allow-all-tools",)
    # Extra directories the agent may access (`--add-dir`); the cwd is always included by the CLI.
    add_dirs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class AgentOptions:
    """Optional caller overrides; unset (None) fields keep the AgentContext defaults."""

    docker_image: str | None = None
    cli_args: tuple[str, ...] | None = None
    # Runs stub adapters that only log to the console: no git, Docker or Copilot process.
    dry_run: bool = False


@dataclass(frozen=True)
class HeadStrategy:
    """No worktree or branch: the agent works directly in the repository directory."""


@dataclass(frozen=True)
class MergeToHeadStrategy:
    """Temp-branch worktree, fast-forward merged into the current HEAD on success, then the temp branch is deleted."""


@dataclass(frozen=True)
class BranchStrategy:
    """Worktree on the named branch; `base_branch` is the start ref for a new branch (default HEAD)."""

    branch: str
    base_branch: str | None = None


GitStrategy = HeadStrategy | MergeToHeadStrategy | BranchStrategy


@dataclass(frozen=True)
class GitOptions:
    """Where the worktree lives (target = root_path/branch) and which strategy creates it."""

    # Must resolve inside the agent cwd; relative paths are taken from the cwd.
    root_path: Path = Path(".worktrees")
    # Repository that git -C targets (multi-repository setups); None uses the agent cwd.
    repository_path: Path | None = None
    strategy: GitStrategy = HeadStrategy()


@dataclass(frozen=True)
class RunContext:
    agent: AgentContext = field(default_factory=AgentContext)


class AgentClient(Protocol):
    """Stable public interface, regardless of enabled wrappers."""

    def run(self, prompt: str, context: RunContext | None = None) -> str: ...

    def close(self) -> None: ...


class CliRunner(Protocol):
    def run(self, prompt: str, context: RunContext) -> str: ...


class GitService(Protocol):
    def open(self, cwd: Path, options: GitOptions) -> AbstractContextManager[Path]: ...


class DockerService(Protocol):
    def configure(self, context: RunContext) -> RunContext: ...


class CopilotAgentClient:
    """Core agent; delegates execution to its runner."""

    def __init__(self, runner: CliRunner, defaults: RunContext | None = None) -> None:
        self._runner = runner
        self._defaults = defaults or RunContext()

    def run(self, prompt: str, context: RunContext | None = None) -> str:
        return self._runner.run(prompt, context or self._defaults)

    def close(self) -> None:
        pass


class AgentWrapper:
    """Composes an AgentClient and delegates common lifecycle operations."""

    def __init__(self, inner: AgentClient, defaults: RunContext | None = None) -> None:
        self._inner = inner
        self._defaults = defaults or RunContext()

    def close(self) -> None:
        self._inner.close()


class GitAgent(AgentWrapper):
    def __init__(
        self,
        inner: AgentClient,
        git: GitService,
        options: GitOptions | None = None,
        defaults: RunContext | None = None,
    ) -> None:
        super().__init__(inner, defaults)
        self._git = git
        self._options = options or GitOptions()

    def run(self, prompt: str, context: RunContext | None = None) -> str:
        original = context or self._defaults
        # The strategy's git lifecycle surrounds the entire delegated execution.
        with self._git.open(original.agent.cwd, self._options) as workdir:
            # The worktree is inside cwd, so the CLI already has access to it.
            moved = replace(original, agent=replace(original.agent, cwd=workdir))
            return self._inner.run(prompt, moved)


class DockerAgent(AgentWrapper):
    def __init__(
        self,
        inner: AgentClient,
        docker: DockerService,
        defaults: RunContext | None = None,
    ) -> None:
        super().__init__(inner, defaults)
        self._docker = docker

    def run(self, prompt: str, context: RunContext | None = None) -> str:
        configured = self._docker.configure(context or self._defaults)
        return self._inner.run(prompt, configured)


class AgentBuilder:
    def __init__(
        self,
        runner: CliRunner,
        git: GitService,
        docker: DockerService,
        agent_context: AgentContext | None = None,
    ) -> None:
        self._runner = runner
        self._git = git
        self._docker = docker
        self._defaults = RunContext(agent_context or AgentContext())
        self._use_git = False
        self._git_options: GitOptions | None = None
        self._use_docker = False

    def with_git(self, options: GitOptions | None = None) -> AgentBuilder:
        self._use_git = True
        self._git_options = options
        return self

    def with_docker(self) -> AgentBuilder:
        self._use_docker = True
        return self

    def create(self) -> AgentClient:
        client: AgentClient = CopilotAgentClient(self._runner, self._defaults)
        if self._use_docker:
            client = DockerAgent(client, self._docker, self._defaults)
        if self._use_git:
            client = GitAgent(client, self._git, self._git_options, self._defaults)
        return client


# --- Stub adapters to demonstrate dependency registration ---


class CopilotCli:
    """Runs the Copilot CLI in the agent cwd; Docker is not launched yet."""

    def command(self, prompt: str, context: RunContext) -> list[str]:
        agent = context.agent
        dirs = [part for directory in agent.add_dirs for part in ("--add-dir", str(directory))]
        return ["copilot", "-p", prompt, *agent.cli_args, *dirs]

    def run(self, prompt: str, context: RunContext) -> str:
        result = subprocess.run(
            self.command(prompt, context),
            cwd=context.agent.cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout


class GitCli:
    """Runs git against a given repository; `run` is injectable for testing."""

    def __init__(
        self,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._run = run

    def fetch(self, repository: Path) -> None:
        self._git(repository, "fetch", "--all", "--prune")

    def add_worktree(self, repository: Path, target: Path, branch: str, base: str = "HEAD") -> None:
        """Validate `branch`, reuse it if it exists locally, else create it, then attach a worktree at `target`."""
        self._git(repository, "check-ref-format", "--branch", branch)
        if not self._succeeds(repository, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
            remote = self._succeeds(repository, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}")
            self._git(repository, "branch", branch, f"origin/{branch}" if remote else base)
        self._git(repository, "worktree", "add", str(target), branch)

    def remove_worktree(self, repository: Path, target: Path) -> None:
        """Detach the worktree at `target`; git refuses when it has uncommitted changes. The branch is kept."""
        self._git(repository, "worktree", "remove", str(target))

    def commit_all(self, worktree: Path, message: str) -> None:
        """Commit every change in the worktree; no-op when there is nothing to commit."""
        self._git(worktree, "add", "-A")
        if not self._succeeds(worktree, "diff", "--cached", "--quiet"):
            self._git(worktree, "commit", "-m", message)

    def merge_ff_only(self, repository: Path, branch: str) -> None:
        self._git(repository, "merge", "--ff-only", branch)

    def delete_branch(self, repository: Path, branch: str) -> None:
        self._git(repository, "branch", "-d", branch)

    def _succeeds(self, repository: Path, *args: str) -> bool:
        return self._invoke(repository, args, check=False).returncode == 0

    def _git(self, repository: Path, *args: str) -> None:
        self._invoke(repository, args, check=True)

    def _invoke(
        self, repository: Path, args: tuple[str, ...], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(repository), *args]
        return self._run(command, check=check, capture_output=True, text=True)


class GitRuntime:
    """Applies the options' strategy through git and yields the directory the agent works in."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    @contextmanager
    def open(self, cwd: Path, options: GitOptions) -> Iterator[Path]:
        repository = (cwd / options.repository_path) if options.repository_path else cwd
        match options.strategy:
            case HeadStrategy():
                yield repository
            case MergeToHeadStrategy():
                branch = f"tmp_{secrets.token_hex(4)}"
                with self._worktree(cwd, repository, options, branch, "HEAD") as target:
                    yield target
                    # Commit before the worktree is removed so the merge carries the agent's edits.
                    self._git.commit_all(target, "Agent changes")
                # Reached only when the run succeeded; on failure the temp branch is kept unmerged.
                self._git.merge_ff_only(repository, branch)
                self._git.delete_branch(repository, branch)
            case BranchStrategy(branch=branch, base_branch=base):
                self._git.fetch(repository)
                with self._worktree(cwd, repository, options, branch, base or "HEAD") as target:
                    yield target

    @contextmanager
    def _worktree(
        self, cwd: Path, repository: Path, options: GitOptions, branch: str, base: str
    ) -> Iterator[Path]:
        root = (cwd / options.root_path).resolve()
        if not root.is_relative_to(cwd.resolve()):
            raise ValueError(f"worktrees root {root} must be inside {cwd}")
        target = root / branch
        self._git.add_worktree(repository, target, branch, base)
        try:
            yield target
        finally:
            self._git.remove_worktree(repository, target)


class DockerRuntime:
    def configure(self, context: RunContext) -> RunContext:
        # Demonstration only: records intent; runner does not launch Docker.
        return replace(context, agent=replace(context.agent, docker_image="agent:latest"))


def _log(message: str) -> None:
    print(f"[dry-run] {message}")


class LoggingGitCli(GitCli):
    """Dry-run GitCli: logs each git command instead of running it; show-ref reports a missing ref."""

    def _invoke(
        self, repository: Path, args: tuple[str, ...], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(repository), *args]
        _log(" ".join(command))
        return subprocess.CompletedProcess(command, 1 if "show-ref" in args else 0, "", "")


class LoggingRunner:
    """Dry-run CliRunner: logs the command instead of running it."""

    def __init__(self, inner: CopilotCli) -> None:
        self._inner = inner

    def run(self, prompt: str, context: RunContext) -> str:
        result = f"cwd={context.agent.cwd} cmd={' '.join(self._inner.command(prompt, context))}"
        _log(result)
        return result


class LoggingDocker:
    """Dry-run DockerService: delegates to the stub and logs the configured image."""

    def __init__(self, inner: DockerService) -> None:
        self._inner = inner

    def configure(self, context: RunContext) -> RunContext:
        configured = self._inner.configure(context)
        _log(f"docker image={configured.agent.docker_image}")
        return configured


def Agent(options: AgentOptions | None = None) -> AgentBuilder:
    """Composition root: register private default dependencies and return builder."""
    agent_context = _apply_options(AgentContext(), options)
    if options is not None and options.dry_run:
        return AgentBuilder(
            runner=LoggingRunner(CopilotCli()),
            git=GitRuntime(LoggingGitCli()),
            docker=LoggingDocker(DockerRuntime()),
            agent_context=agent_context,
        )
    return AgentBuilder(
        runner=CopilotCli(),
        git=GitRuntime(GitCli()),
        docker=DockerRuntime(),
        agent_context=agent_context,
    )


def _apply_options(context: AgentContext, options: AgentOptions | None) -> AgentContext:
    if options is None:
        return context
    if options.docker_image is not None:
        context = replace(context, docker_image=options.docker_image)
    if options.cli_args is not None:
        context = replace(context, cli_args=options.cli_args)
    return context


def repo_agent(repo: str, branch: str | None = None) -> AgentClient:
    """One worktree-isolated agent for workspace/<repo>, with worktrees in workspace/<repo>.worktrees."""
    workspace = Path("workspace")
    options = GitOptions(
        root_path=workspace / f"{repo}.worktrees",
        repository_path=workspace / repo,
        strategy=BranchStrategy(branch) if branch else MergeToHeadStrategy(),
    )
    return Agent().with_git(options).with_docker().create()


if __name__ == "__main__":
    # Run from the harness dir (the agent cwd). `--dry-run` only logs; otherwise real git runs in workspace/repo1 and repo2.
    if "--dry-run" in sys.argv:
        _DRY = AgentOptions(dry_run=True)
        _root = Path("workspace/repo1.worktrees")
        _repo = Path("workspace/repo1")
        # Agent(_DRY).with_git(GitOptions(_root, _repo, HeadStrategy())).create().run("Implement ticket #123 in repo1")
        Agent(_DRY).with_git(GitOptions(_root, _repo, MergeToHeadStrategy())).create().run("Implement ticket #123 in repo1")
        # Agent(_DRY).with_git(GitOptions(_root, _repo, BranchStrategy("loop/ticket-123", "main"))).create().run("Implement ticket #123 in repo1")
        sys.exit()
    # print(repo_agent("repo1", "loop/ticket-123").run("Implement ticket #123 in repo1"))
    # print(repo_agent("repo2", "loop/ticket-123").run("Implement ticket #123 in repo2"))

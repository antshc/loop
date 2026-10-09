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
from typing import Iterator, Protocol, Self


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
class WorktreesOptions:
    """Where and under which new branch the worktree is created: target = root_path/branch."""

    # Must resolve inside the agent cwd; relative paths are taken from the cwd.
    root_path: Path = Path(".worktrees")
    # None generates `feat_<hex>` per run, so each run gets its own branch.
    branch: str | None = None
    # Repository that git -C targets (multi-repository setups); None uses the agent cwd.
    repository_path: Path | None = None


@dataclass(frozen=True)
class RunContext:
    agent: AgentContext = field(default_factory=AgentContext)


class AgentClient(Protocol):
    """Stable public interface, regardless of enabled wrappers."""

    def run(self, prompt: str, context: RunContext | None = None) -> str: ...

    def close(self) -> None: ...


class CliRunner(Protocol):
    def run(self, prompt: str, context: RunContext) -> str: ...


class WorktreeService(Protocol):
    def open(self, cwd: Path, options: WorktreesOptions) -> AbstractContextManager[Path]: ...


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


class WorktreeAgent(AgentWrapper):
    def __init__(
        self,
        inner: AgentClient,
        worktrees: WorktreeService,
        options: WorktreesOptions | None = None,
        defaults: RunContext | None = None,
    ) -> None:
        super().__init__(inner, defaults)
        self._worktrees = worktrees
        self._options = options or WorktreesOptions()

    def run(self, prompt: str, context: RunContext | None = None) -> str:
        original = context or self._defaults
        # The worktree lifecycle surrounds the entire delegated execution.
        with self._worktrees.open(original.agent.cwd, self._options):
            # The worktree is inside cwd, so the CLI already has access to it.
            return self._inner.run(prompt, original)


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
        worktrees: WorktreeService,
        docker: DockerService,
        agent_context: AgentContext | None = None,
    ) -> None:
        self._runner = runner
        self._worktrees = worktrees
        self._docker = docker
        self._defaults = RunContext(agent_context or AgentContext())
        self._use_worktrees = False
        self._worktrees_options: WorktreesOptions | None = None
        self._use_docker = False

    def with_worktrees(self, options: WorktreesOptions | None = None) -> Self:
        self._use_worktrees = True
        self._worktrees_options = options
        return self

    def with_docker(self) -> Self:
        self._use_docker = True
        return self

    def create(self) -> AgentClient:
        client: AgentClient = CopilotAgentClient(self._runner, self._defaults)
        if self._use_docker:
            client = DockerAgent(client, self._docker, self._defaults)
        if self._use_worktrees:
            client = WorktreeAgent(client, self._worktrees, self._worktrees_options, self._defaults)
        return client


# --- Stub adapters to demonstrate dependency registration ---


class CopilotCli:
    def run(self, prompt: str, context: RunContext) -> str:
        # Demonstration only: a real adapter would invoke the Copilot CLI.
        agent = context.agent
        mode = f"docker:{agent.docker_image}" if agent.docker_image else "local"
        dirs = [part for directory in agent.add_dirs for part in ("--add-dir", str(directory))]
        args = " ".join(("copilot", "-p", prompt, *agent.cli_args, *dirs))
        return f"[{mode}] cwd={agent.cwd} cmd={args}"


class GitCli:
    """Runs git against a given repository; `run` is injectable for testing."""

    def __init__(
        self,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._run = run

    def create_worktree(self, repository: Path, target: Path, branch: str, base: str = "main") -> None:
        """Fetch, validate `branch`, point it at its start ref, and attach a worktree at `target`."""
        self._git(repository, "fetch", "--all", "--prune")
        self._git(repository, "check-ref-format", "--branch", branch)
        start_ref = self._start_ref(repository, branch, base)
        # Move an existing local branch; create it otherwise.
        if self._succeeds(repository, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
            self._git(repository, "branch", "-f", branch, start_ref)
        else:
            self._git(repository, "branch", branch, start_ref)
        self._git(repository, "worktree", "add", str(target), branch)

    def remove_worktree(self, repository: Path, target: Path) -> None:
        """Detach the worktree at `target`; git refuses when it has uncommitted changes. The branch is kept."""
        self._git(repository, "worktree", "remove", str(target))

    def _start_ref(self, repository: Path, branch: str, base: str) -> str:
        if self._succeeds(repository, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"):
            return f"origin/{branch}"
        return f"origin/{base}"

    def _succeeds(self, repository: Path, *args: str) -> bool:
        return self._invoke(repository, args, check=False).returncode == 0

    def _git(self, repository: Path, *args: str) -> None:
        self._invoke(repository, args, check=True)

    def _invoke(
        self, repository: Path, args: tuple[str, ...], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(repository), *args]
        return self._run(command, check=check, capture_output=True, text=True)


class WorktreesRuntime:
    """Creates the worktree at root_path/branch through git, yields its path, and removes it on exit."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    @contextmanager
    def open(self, cwd: Path, options: WorktreesOptions) -> Iterator[Path]:
        root = (cwd / options.root_path).resolve()
        if not root.is_relative_to(cwd.resolve()):
            raise ValueError(f"worktrees root {root} must be inside {cwd}")
        branch = options.branch or f"feat_{secrets.token_hex(4)}"
        target = root / branch
        repository = (cwd / options.repository_path) if options.repository_path else cwd
        self._git.create_worktree(repository, target, branch)
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
    """Dry-run CliRunner: delegates to the stub and logs its result."""

    def __init__(self, inner: CliRunner) -> None:
        self._inner = inner

    def run(self, prompt: str, context: RunContext) -> str:
        result = self._inner.run(prompt, context)
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
            worktrees=WorktreesRuntime(LoggingGitCli()),
            docker=LoggingDocker(DockerRuntime()),
            agent_context=agent_context,
        )
    return AgentBuilder(
        runner=CopilotCli(),
        worktrees=WorktreesRuntime(GitCli()),
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
    options = WorktreesOptions(
        root_path=workspace / f"{repo}.worktrees",
        branch=branch,
        repository_path=workspace / repo,
    )
    return Agent().with_worktrees(options).with_docker().create()


if __name__ == "__main__":
    # Run from the harness dir (the agent cwd). `--dry-run` only logs; otherwise real git runs in workspace/repo1 and repo2.
    if "--dry-run" in sys.argv:
        _DRY = AgentOptions(dry_run=True)
        _opts = WorktreesOptions(Path("workspace/repo1.worktrees"), repository_path=Path("workspace/repo1"))
        # Agent(_DRY).create().run("Implement ticket #123 in harness")
        Agent(_DRY).with_worktrees(_opts).create().run("Implement ticket #123 in repo1")
        # print(Agent(_DRY).with_worktrees(_opts).with_docker().create().run("Implement ticket #123 in repo1"))
        sys.exit()
    # print(repo_agent("repo1", "loop/ticket-123").run("Implement ticket #123 in repo1"))
    # print(repo_agent("repo2", "loop/ticket-123").run("Implement ticket #123 in repo2"))

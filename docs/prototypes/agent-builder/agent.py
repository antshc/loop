"""Standalone contract prototype: Agent builder with composed execution wrappers.

No real Git worktree, Docker container, or Copilot process is started here.
"""

from __future__ import annotations

import secrets
import subprocess
import sys
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterator, Protocol


@dataclass(frozen=True)
class AgentRequest:
    """One agent execution; a set `session_id` resumes that session, None starts a new one."""

    prompt: str
    session_id: str | None = None
    # Per-run Copilot CLI settings; None adds no flag, so the provider default applies.
    model: str | None = None
    reasoning_effort: str | None = None
    context: str | None = None


@dataclass(frozen=True)
class AgentResult:
    """Final outcome of one execution, carrying the session to pass back for a follow-up."""

    output: str
    session_id: str
    exit_code: int


@dataclass(frozen=True)
class AgentSession:
    """Identifies persistent agent context, independent of Docker or CLI processes."""

    id: str


@dataclass(frozen=True)
class SessionOptions:
    """Workflow configuration: runs share one session per key; the default key is the run cwd (the worktree)."""

    key: str | None = None


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


class GitStrategy(Protocol):
    """Owns one git lifecycle around a run and yields the directory the agent works in."""

    def open(
        self, git: GitCli, cwd: Path, repository: Path, options: GitOptions
    ) -> AbstractContextManager[Path]: ...


@dataclass(frozen=True)
class HeadStrategy:
    """No worktree or branch: the agent works directly in the repository directory."""

    @contextmanager
    def open(self, git: GitCli, cwd: Path, repository: Path, options: GitOptions) -> Iterator[Path]:
        yield repository


@dataclass(frozen=True)
class MergeToHeadStrategy:
    """Temp-branch worktree, fast-forward merged into the current HEAD on success, then the temp branch is deleted."""

    @contextmanager
    def open(self, git: GitCli, cwd: Path, repository: Path, options: GitOptions) -> Iterator[Path]:
        branch = f"tmp_{secrets.token_hex(4)}"
        with _worktree(git, cwd, repository, options, branch, "HEAD") as target:
            yield target
            # Safety net: the prompt normally commits; this catches edits it left behind before the worktree is removed.
            git.commit_all(target, options.commit_message)
        # Reached only when the run succeeded; on failure the temp branch is kept unmerged.
        git.merge_ff_only(repository, branch)
        git.delete_branch(repository, branch)


@dataclass(frozen=True)
class BranchStrategy:
    """Worktree on the named branch; `base_branch` is the start ref for a new branch (default HEAD)."""

    branch: str
    base_branch: str | None = None

    @contextmanager
    def open(self, git: GitCli, cwd: Path, repository: Path, options: GitOptions) -> Iterator[Path]:
        git.fetch(repository)
        with _worktree(git, cwd, repository, options, self.branch, self.base_branch or "HEAD") as target:
            yield target
            # Safety net: git refuses to remove a dirty worktree.
            git.commit_all(target, options.commit_message)


@dataclass(frozen=True)
class GitOptions:
    """Where the worktree lives (target = root_path/branch) and which strategy creates it."""

    # Must resolve inside the agent cwd; relative paths are taken from the cwd.
    root_path: Path = Path(".worktrees")
    # Repository that git -C targets (multi-repository setups); None uses the agent cwd.
    repository_path: Path | None = None
    strategy: GitStrategy = HeadStrategy()
    # Message for the safety-net commit of changes the agent left uncommitted.
    commit_message: str = "agent: commit uncommitted changes"


@dataclass(frozen=True)
class RunContext:
    agent: AgentContext = field(default_factory=AgentContext)


class AgentClient(Protocol):
    """Stable public interface, regardless of enabled wrappers."""

    def run(self, request: AgentRequest, context: RunContext | None = None) -> AgentResult: ...

    def close(self) -> None: ...


class CliRunner(Protocol):
    def run(self, request: AgentRequest, context: RunContext) -> AgentResult: ...


class SessionStore(Protocol):
    def get(self, key: str) -> AgentSession | None: ...

    def save(self, key: str, session: AgentSession) -> None: ...


class GitService(Protocol):
    def open(self, cwd: Path, options: GitOptions) -> AbstractContextManager[Path]: ...


class DockerService(Protocol):
    def configure(self, context: RunContext) -> RunContext: ...


class CopilotAgentClient:
    """Core agent; delegates execution to its runner."""

    def __init__(self, runner: CliRunner, defaults: RunContext | None = None) -> None:
        self._runner = runner
        self._defaults = defaults or RunContext()

    def run(self, request: AgentRequest, context: RunContext | None = None) -> AgentResult:
        return self._runner.run(request, context or self._defaults)

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

    def run(self, request: AgentRequest, context: RunContext | None = None) -> AgentResult:
        original = context or self._defaults
        # The strategy's git lifecycle surrounds the entire delegated execution.
        with self._git.open(original.agent.cwd, self._options) as workdir:
            # The worktree is inside cwd, so the CLI already has access to it.
            moved = replace(original, agent=replace(original.agent, cwd=workdir))
            return self._inner.run(request, moved)


class SessionAgent(AgentWrapper):
    """Continues one session across runs that share a key; an explicit request session wins."""

    def __init__(
        self,
        inner: AgentClient,
        sessions: SessionStore,
        options: SessionOptions | None = None,
        defaults: RunContext | None = None,
    ) -> None:
        super().__init__(inner, defaults)
        self._sessions = sessions
        self._options = options or SessionOptions()

    def run(self, request: AgentRequest, context: RunContext | None = None) -> AgentResult:
        effective = context or self._defaults
        key = self._options.key or str(effective.agent.cwd)
        if request.session_id is None:
            known = self._sessions.get(key)
            if known is not None:
                request = replace(request, session_id=known.id)
        result = self._inner.run(request, effective)
        self._sessions.save(key, AgentSession(result.session_id))
        return result


class DockerAgent(AgentWrapper):
    def __init__(
        self,
        inner: AgentClient,
        docker: DockerService,
        defaults: RunContext | None = None,
    ) -> None:
        super().__init__(inner, defaults)
        self._docker = docker

    def run(self, request: AgentRequest, context: RunContext | None = None) -> AgentResult:
        configured = self._docker.configure(context or self._defaults)
        return self._inner.run(request, configured)


class AgentBuilder:
    def __init__(
        self,
        runner: CliRunner,
        git: GitService,
        docker: DockerService,
        agent_context: AgentContext | None = None,
        sessions: SessionStore | None = None,
    ) -> None:
        self._runner = runner
        self._git = git
        self._docker = docker
        self._sessions = sessions or MemorySessionStore()
        self._defaults = RunContext(agent_context or AgentContext())
        self._use_git = False
        self._git_options: GitOptions | None = None
        self._use_docker = False
        self._use_session = False
        self._session_options: SessionOptions | None = None

    def with_git(self, options: GitOptions | None = None) -> AgentBuilder:
        self._use_git = True
        self._git_options = options
        return self

    def with_docker(self) -> AgentBuilder:
        self._use_docker = True
        return self

    def with_session(self, options: SessionOptions | None = None) -> AgentBuilder:
        self._use_session = True
        self._session_options = options
        return self

    def create(self) -> AgentClient:
        client: AgentClient = CopilotAgentClient(self._runner, self._defaults)
        if self._use_docker:
            client = DockerAgent(client, self._docker, self._defaults)
        if self._use_session:
            client = SessionAgent(client, self._sessions, self._session_options, self._defaults)
        if self._use_git:
            client = GitAgent(client, self._git, self._git_options, self._defaults)
        return client


# --- Stub adapters to demonstrate dependency registration ---


class MemorySessionStore:
    """Process-local session store; sessions are not persisted across runs of the process."""

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}

    def get(self, key: str) -> AgentSession | None:
        return self._sessions.get(key)

    def save(self, key: str, session: AgentSession) -> None:
        self._sessions[key] = session


class CopilotCli:
    """Runs the Copilot CLI in the agent cwd; Docker is not launched yet."""

    def command(self, request: AgentRequest, session_id: str, context: RunContext) -> list[str]:
        agent = context.agent
        dirs = [part for directory in agent.add_dirs for part in ("--add-dir", str(directory))]
        settings = {"--model": request.model, "--reasoning-effort": request.reasoning_effort, "--context": request.context}
        run_args = [part for flag, value in settings.items() if value is not None for part in (flag, value)]
        return ["copilot", "-p", request.prompt, f"--resume={session_id}", *agent.cli_args, *run_args, *dirs]

    def run(self, request: AgentRequest, context: RunContext) -> AgentResult:
        # Assumption (unverified): the CLI accepts a caller-generated id on first use.
        session_id = request.session_id or uuid.uuid4().hex
        result = subprocess.run(
            self.command(request, session_id, context),
            cwd=context.agent.cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return AgentResult(result.stdout, session_id, result.returncode)


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


@contextmanager
def _worktree(
    git: GitCli, cwd: Path, repository: Path, options: GitOptions, branch: str, base: str
) -> Iterator[Path]:
    root = (cwd / options.root_path).resolve()
    if not root.is_relative_to(cwd.resolve()):
        raise ValueError(f"worktrees root {root} must be inside {cwd}")
    target = root / branch
    git.add_worktree(repository, target, branch, base)
    try:
        yield target
    finally:
        git.remove_worktree(repository, target)


class GitRuntime:
    """Delegates to the options' strategy and yields the directory the agent works in."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    def open(self, cwd: Path, options: GitOptions) -> AbstractContextManager[Path]:
        repository = (cwd / options.repository_path) if options.repository_path else cwd
        return options.strategy.open(self._git, cwd, repository, options)


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

    def run(self, request: AgentRequest, context: RunContext) -> AgentResult:
        session_id = request.session_id or uuid.uuid4().hex
        output = f"cwd={context.agent.cwd} cmd={' '.join(self._inner.command(request, session_id, context))}"
        _log(output)
        return AgentResult(output, session_id, 0)


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
        Agent(_DRY).with_git(GitOptions(_root, _repo, MergeToHeadStrategy())).create().run(AgentRequest("Implement ticket #123 in repo1"))
        # Plan, then implement: the second run resumes the first run's session and builds on its output.
        _client = Agent(_DRY).with_git(GitOptions(_root, _repo, BranchStrategy("loop/ticket-123", "main"))).with_session().create()
        _plan = _client.run(AgentRequest("Plan ticket #123 in repo1", model="claude-sonnet-5.5", reasoning_effort="max", context="long_context"))
        _client.run(AgentRequest(f"Implement this plan:\n{_plan.output}", _plan.session_id, model="claude-sonnet-5.5", reasoning_effort="high"))
        # Without with_session() and without a session_id, each run starts a new session.
        _fresh = Agent(_DRY).with_git(GitOptions(_root, _repo, BranchStrategy("loop/ticket-123", "main"))).create()
        _fresh.run(AgentRequest("Plan ticket #123 in repo1"))
        _fresh.run(AgentRequest("Implement ticket #123 in repo1"))
        sys.exit()
    # print(repo_agent("repo1", "loop/ticket-123").run("Implement ticket #123 in repo1"))
    # print(repo_agent("repo2", "loop/ticket-123").run("Implement ticket #123 in repo2"))

"""Standalone contract prototype: Agent builder with composed execution wrappers.

No real Git worktree, Docker container, or Copilot process is started here.
"""

from __future__ import annotations

import json
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


@dataclass(frozen=True)
class AgentResult:
    """Final outcome of one execution, carrying the session to pass back for a follow-up."""

    output: str
    session_id: str
    exit_code: int


class AgentCli(Protocol):
    """Adapter for one agent CLI; the library runs the process, sessions, dry-run and Docker."""

    # Unique per CLI: it scopes session keys.
    name: str

    def new_session_id(self) -> str | None:
        """A caller-chosen id for a new session, or None when the CLI assigns its own."""
        ...

    def command(
        self, request: AgentRequest, profile: AgentProfile, session_id: str | None, context: RunContext
    ) -> list[str]: ...

    def parse(self, stdout: str, session_id: str | None, exit_code: int) -> AgentResult:
        """Must return the session id to resume."""
        ...


@dataclass(frozen=True)
class AgentProfile:
    """One CLI plus its settings; None adds no flag, so the CLI default applies."""

    cli: AgentCli
    model: str | None = None
    reasoning_effort: str | None = None
    context: str | None = None
    args: tuple[str, ...] = ()


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
    """Agent-level settings shared by every CLI."""

    cwd: Path = field(default_factory=Path.cwd)
    docker_image: str | None = None
    # Extra directories the agent may access (`--add-dir`); the cwd is always included by the CLI.
    add_dirs: tuple[Path, ...] = ()


@dataclass(frozen=True)
class AgentOptions:
    """Optional caller overrides; unset (None) fields keep the AgentContext defaults."""

    docker_image: str | None = None
    # Runs stub adapters that only log to the console: no git, Docker or CLI process.
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
    def run(self, profile: AgentProfile, request: AgentRequest, context: RunContext) -> AgentResult: ...


class SessionStore(Protocol):
    def get(self, key: str) -> AgentSession | None: ...

    def save(self, key: str, session: AgentSession) -> None: ...


class GitService(Protocol):
    def open(self, cwd: Path, options: GitOptions) -> AbstractContextManager[Path]: ...


class DockerService(Protocol):
    def configure(self, context: RunContext) -> RunContext: ...


class CliAgentClient:
    """Core agent bound to one CLI profile; delegates execution to its runner."""

    def __init__(self, runner: CliRunner, profile: AgentProfile, defaults: RunContext | None = None) -> None:
        self._runner = runner
        self._profile = profile
        self._defaults = defaults or RunContext()

    def run(self, request: AgentRequest, context: RunContext | None = None) -> AgentResult:
        return self._runner.run(self._profile, request, context or self._defaults)

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
    """Continues one session per key and CLI across runs; an explicit request session wins."""

    def __init__(
        self,
        inner: AgentClient,
        sessions: SessionStore,
        cli_name: str,
        options: SessionOptions | None = None,
        defaults: RunContext | None = None,
    ) -> None:
        super().__init__(inner, defaults)
        self._sessions = sessions
        self._cli_name = cli_name
        self._options = options or SessionOptions()

    def run(self, request: AgentRequest, context: RunContext | None = None) -> AgentResult:
        effective = context or self._defaults
        key = f"{self._options.key or effective.agent.cwd}:{self._cli_name}"
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


class Worktree:
    """One live worktree shared by clients of several CLIs; valid only inside `AgentBuilder.open()`."""

    def __init__(self, path: Path, build: Callable[[AgentProfile, RunContext], AgentClient], defaults: RunContext) -> None:
        self.path = path
        self._build = build
        self._defaults = replace(defaults, agent=replace(defaults.agent, cwd=path))
        self._closed = False

    def agent(self, profile: AgentProfile | None = None) -> AgentClient:
        if self._closed:
            raise RuntimeError("worktree closed")
        return self._build(profile or DEFAULT, self._defaults)


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

    def _stack(self, profile: AgentProfile, defaults: RunContext) -> AgentClient:
        client: AgentClient = CliAgentClient(self._runner, profile, defaults)
        if self._use_docker:
            client = DockerAgent(client, self._docker, defaults)
        if self._use_session:
            client = SessionAgent(client, self._sessions, profile.cli.name, self._session_options, defaults)
        return client

    @contextmanager
    def open(self) -> Iterator[Worktree]:
        """Run the git strategy once and share the worktree between clients of any CLI."""
        worktree: Worktree | None = None
        try:
            if self._use_git:
                with self._git.open(self._defaults.agent.cwd, self._git_options or GitOptions()) as path:
                    worktree = Worktree(path, self._stack, self._defaults)
                    yield worktree
            else:
                worktree = Worktree(self._defaults.agent.cwd, self._stack, self._defaults)
                yield worktree
        finally:
            if worktree is not None:
                worktree._closed = True

    def create(self, profile: AgentProfile | None = None) -> AgentClient:
        """One-shot client: a separate git lifecycle per run()."""
        client = self._stack(profile or DEFAULT, self._defaults)
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
    """Copilot CLI adapter."""

    name = "copilot"

    def __init__(self, args: tuple[str, ...] = ("--allow-all-tools",)) -> None:
        self._args = args

    def new_session_id(self) -> str | None:
        # Assumption (unverified): the CLI accepts a caller-generated id on first use.
        return uuid.uuid4().hex

    def command(
        self, request: AgentRequest, profile: AgentProfile, session_id: str | None, context: RunContext
    ) -> list[str]:
        dirs = [part for directory in context.agent.add_dirs for part in ("--add-dir", str(directory))]
        settings = {"--model": profile.model, "--reasoning-effort": profile.reasoning_effort, "--context": profile.context}
        run_args = [part for flag, value in settings.items() if value is not None for part in (flag, value)]
        return ["copilot", "-p", request.prompt, f"--resume={session_id}", *self._args, *run_args, *profile.args, *dirs]

    def parse(self, stdout: str, session_id: str | None, exit_code: int) -> AgentResult:
        return AgentResult(stdout, session_id or "", exit_code)


def _parse_codex_events(stdout: str) -> tuple[str | None, str]:
    """Return (thread id, last agent message) from `codex exec --json` JSONL output."""
    thread_id: str | None = None
    message = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        elif event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message":
            message = event["item"].get("text", "")
    return thread_id, message


class CodexCli:
    """Codex CLI adapter; Codex assigns its own thread id."""

    name = "codex"

    def __init__(self, args: tuple[str, ...] = ("--sandbox", "workspace-write")) -> None:
        self._args = args

    def new_session_id(self) -> str | None:
        return None

    def command(
        self, request: AgentRequest, profile: AgentProfile, session_id: str | None, context: RunContext
    ) -> list[str]:
        dirs = [part for directory in context.agent.add_dirs for part in ("--add-dir", str(directory))]
        run_args: list[str] = []
        if profile.model is not None:
            run_args += ["--model", profile.model]
        if profile.reasoning_effort is not None:
            run_args += ["-c", f"model_reasoning_effort={profile.reasoning_effort}"]
        head = ["codex", "exec", "--json", *self._args, *run_args, *profile.args, *dirs]
        if session_id is not None:
            return [*head[:2], "resume", session_id, *head[2:], request.prompt]
        return [*head, request.prompt]

    def parse(self, stdout: str, session_id: str | None, exit_code: int) -> AgentResult:
        thread_id, message = _parse_codex_events(stdout)
        return AgentResult(message, thread_id or session_id or "", exit_code)


copilot = CopilotCli()
codex = CodexCli()
DEFAULT = AgentProfile(copilot)


class ProcessCliRunner:
    """Runs any AgentCli as a process in the agent cwd; `run` is injectable for testing."""

    def __init__(self, *, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self._run = run

    def run(self, profile: AgentProfile, request: AgentRequest, context: RunContext) -> AgentResult:
        session_id = request.session_id or profile.cli.new_session_id()
        argv = profile.cli.command(request, profile, session_id, context)
        result = self._run(argv, cwd=context.agent.cwd, check=True, capture_output=True, text=True)
        return profile.cli.parse(result.stdout, session_id, result.returncode)


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

    def run(self, profile: AgentProfile, request: AgentRequest, context: RunContext) -> AgentResult:
        session_id = request.session_id or profile.cli.new_session_id()
        command = profile.cli.command(request, profile, session_id, context)
        output = f"cwd={context.agent.cwd} cmd={' '.join(command)}"
        session_id = session_id or uuid.uuid4().hex
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
            runner=LoggingRunner(),
            git=GitRuntime(LoggingGitCli()),
            docker=LoggingDocker(DockerRuntime()),
            agent_context=agent_context,
        )
    return AgentBuilder(
        runner=ProcessCliRunner(),
        git=GitRuntime(GitCli()),
        docker=DockerRuntime(),
        agent_context=agent_context,
    )


def _apply_options(context: AgentContext, options: AgentOptions | None) -> AgentContext:
    if options is None:
        return context
    if options.docker_image is not None:
        context = replace(context, docker_image=options.docker_image)
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
        # One-shot: one git lifecycle per run().
        Agent(_DRY).with_git(GitOptions(_root, _repo, MergeToHeadStrategy())).create().run(AgentRequest("Implement ticket #123 in repo1"))
        # Role profiles; which CLI and model fill a role is a workflow choice.
        PLANNER = AgentProfile(copilot, "claude-opus-4.5", "high")
        DEVELOPER = AgentProfile(codex, "gpt-5-codex", "high")
        REVIEWER = AgentProfile(copilot, "claude-sonnet-4.5")
        # Shared worktree: one git lifecycle, several CLIs; sessions are kept per CLI.
        _shared = Agent(_DRY).with_git(GitOptions(_root, _repo, BranchStrategy("loop/ticket-123", "main"))).with_session()
        with _shared.open() as _wt:
            _plan = _wt.agent(PLANNER).run(AgentRequest("Plan ticket #123 in repo1"))
            _developer = _wt.agent(DEVELOPER)
            _developer.run(AgentRequest(f"Implement this plan:\n{_plan.output}"))
            _developer.run(AgentRequest("Fix failing tests"))  # resumes the Codex session
            _wt.agent(REVIEWER).run(AgentRequest("Review the diff against the plan"))
        # Without with_session() and without a session_id, each run starts a new session.
        _fresh = Agent(_DRY).with_git(GitOptions(_root, _repo, BranchStrategy("loop/ticket-123", "main"))).create()
        _fresh.run(AgentRequest("Plan ticket #123 in repo1"))
        _fresh.run(AgentRequest("Implement ticket #123 in repo1"))
        sys.exit()
    # print(repo_agent("repo1", "loop/ticket-123").run("Implement ticket #123 in repo1"))
    # print(repo_agent("repo2", "loop/ticket-123").run("Implement ticket #123 in repo2"))

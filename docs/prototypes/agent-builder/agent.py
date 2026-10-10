"""Standalone contract prototype: Agent builder with composed execution wrappers.

No real Git worktree, Docker container, or Copilot process is started here.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Iterator, Protocol


@dataclass(frozen=True)
class SessionName:
    """Caller-chosen name of a session; one name is an independent session per CLI."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or any(char.isspace() for char in self.value):
            raise ValueError(f"invalid session name: {self.value!r}")

    @classmethod
    def new(cls) -> SessionName:
        return cls(f"loop-{uuid.uuid4().hex}")


@dataclass(frozen=True)
class NativeHandle:
    """The id one CLI resumes a session by; only adapters read `value`."""

    cli: str
    value: str


@dataclass(frozen=True)
class Start:
    """Turn that starts a session under `name`."""

    name: SessionName


@dataclass(frozen=True)
class Resume:
    """Turn that continues the session behind `handle`."""

    name: SessionName
    handle: NativeHandle


Turn = Start | Resume


@dataclass(frozen=True)
class CliOutcome:
    """Parsed CLI output plus the handle to resume by."""

    output: str
    handle: NativeHandle
    exit_code: int


class SessionHandleMissing(Exception):
    """The CLI output carried no handle to resume by."""


class SessionCliMismatch(Exception):
    """A stored handle belongs to a different CLI than the one running."""


@dataclass(frozen=True)
class AgentRequest:
    """One agent execution."""

    prompt: str


@dataclass(frozen=True)
class AgentResult:
    """Final outcome of one execution, carrying the session name it ran under."""

    output: str
    session: SessionName
    exit_code: int


class AgentCliHookPoint(StrEnum):
    """Moment in a CLI run at which a native hook fires."""

    SESSION_START = "session-start"
    SESSION_END = "session-end"
    AGENT_STOP = "agent-stop"
    PROMPT_SUBMITTED = "prompt-submitted"
    PRE_TOOL = "pre-tool"
    POST_TOOL = "post-tool"
    SUBAGENT_START = "subagent-start"
    SUBAGENT_STOP = "subagent-stop"
    PRE_COMPACT = "pre-compact"


@dataclass(frozen=True)
class AgentCliHook:
    """Base of shell commands run by the CLI's native hook at one fixed `point`; observe-only. Use a subclass."""

    command: str
    timeout_sec: int = 30
    point: ClassVar[AgentCliHookPoint]

    def __post_init__(self) -> None:
        if not hasattr(type(self), "point"):
            raise TypeError(f"{type(self).__name__} fixes no hook point; use a point subclass")
        if not self.command:
            raise ValueError("hook command must not be empty")
        if self.timeout_sec <= 0:
            raise ValueError("hook timeout_sec must be positive")


class SessionStartAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.SESSION_START


class SessionEndAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.SESSION_END


class AgentStopAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.AGENT_STOP


class PromptSubmittedAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.PROMPT_SUBMITTED


class PreToolAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.PRE_TOOL


class PostToolAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.POST_TOOL


class SubagentStartAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.SUBAGENT_START


class SubagentStopAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.SUBAGENT_STOP


class PreCompactAgentCliHook(AgentCliHook):
    point = AgentCliHookPoint.PRE_COMPACT


class UnsupportedAgentCliHookPoint(Exception):
    """A requested hook point cannot fire in the CLI."""


@dataclass(frozen=True)
class AgentCliHookWiring:
    """What the runner applies around one CLI process; `files` paths are relative to the agent cwd."""

    args: tuple[str, ...] = ()
    files: Mapping[Path, str] = field(default_factory=dict)
    git_excludes: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


_SHIM = Path(__file__).with_name("agent_cli_hook.py").resolve()


def _ordered(hooks: tuple[AgentCliHook, ...]) -> list[tuple[AgentCliHookPoint, AgentCliHook]]:
    """(point, hook) pairs in point-enum order, then declaration order."""
    return [(point, hook) for point in AgentCliHookPoint for hook in hooks if hook.point is point]


def _shim_command(cli: str, point: AgentCliHookPoint, turn: Turn, hook: AgentCliHook) -> str:
    return shlex.join(
        [sys.executable, str(_SHIM), "--cli", cli, "--point", point.value, "--session", turn.name.value, "--", hook.command]
    )


class AgentCli(Protocol):
    """Adapter for one agent CLI; the library runs the process, sessions, dry-run and Docker."""

    # Unique per CLI: it scopes session keys.
    name: str
    hook_points: frozenset[AgentCliHookPoint]

    def hook_wiring(self, hooks: tuple[AgentCliHook, ...], turn: Turn, workdir: Path) -> AgentCliHookWiring:
        """Pure translation of `hooks` into this CLI's native wiring; does no I/O."""
        ...

    def native_point(self, point: AgentCliHookPoint) -> str:
        """The CLI's own name for `point`."""
        ...

    def handle_for_new(self, name: SessionName) -> NativeHandle | None:
        """The handle the CLI uses for a session started under `name`, or None when the CLI assigns its own."""
        ...

    def command(
        self, request: AgentRequest, profile: AgentProfile, turn: Turn, context: RunContext
    ) -> list[str]: ...

    def parse(self, stdout: str, turn: Turn, exit_code: int) -> CliOutcome:
        """Raises SessionHandleMissing when no handle can be determined."""
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


DEFAULT_LOOP_HOOK_TIMEOUT_SEC = 120.0


class LoopHookPoint(StrEnum):
    """Moment in a Loop run at which a Loop hook runs."""

    WORKTREE_READY = "worktree-ready"
    WORKTREE_REMOVING = "worktree-removing"
    RUN_FINISHED = "run-finished"


@dataclass(frozen=True)
class LoopHook:
    """Base of shell commands run by Loop at one fixed `point`. Use a subclass."""

    command: str
    timeout_sec: float = DEFAULT_LOOP_HOOK_TIMEOUT_SEC
    point: ClassVar[LoopHookPoint]

    def __post_init__(self) -> None:
        if not hasattr(type(self), "point"):
            raise TypeError(f"{type(self).__name__} fixes no hook point; use a point subclass")
        if not self.command:
            raise ValueError("Loop hook command must not be empty")
        if self.timeout_sec <= 0:
            raise ValueError("Loop hook timeout_sec must be positive")


class WorktreeReadyLoopHook(LoopHook):
    """Runs in a fresh worktree before the agent starts; may write only gitignored paths."""

    point = LoopHookPoint.WORKTREE_READY


class WorktreeRemovingLoopHook(LoopHook):
    """Runs in the worktree after the safety-net commit and before its removal, on success and failure."""

    point = LoopHookPoint.WORKTREE_REMOVING


class RunFinishedLoopHook(LoopHook):
    """Runs in the repository after the worktree is removed and merged; only when the run succeeded."""

    point = LoopHookPoint.RUN_FINISHED


class LoopHookError(Exception):
    def __init__(self, point: LoopHookPoint, command: str, output: str) -> None:
        super().__init__(f"{point.value} hook failed: {command}\n{output}")
        self.point = point
        self.command = command
        self.output = output


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
    # Loop hooks of any point; each runs at its own point, in declared order.
    loop_hooks: tuple[LoopHook, ...] = ()

    def __post_init__(self) -> None:
        if self.loop_hooks and isinstance(self.strategy, HeadStrategy):
            raise ValueError("Loop hooks need a worktree strategy")

    def hooks_at(self, point: LoopHookPoint) -> tuple[LoopHook, ...]:
        return tuple(hook for hook in self.loop_hooks if hook.point is point)


@dataclass(frozen=True)
class RunContext:
    agent: AgentContext = field(default_factory=AgentContext)
    agent_cli_hooks: tuple[AgentCliHook, ...] = ()


class AgentClient(Protocol):
    """Stable public interface, regardless of enabled wrappers."""

    def run(self, request: AgentRequest, context: RunContext | None = None) -> AgentResult: ...

    def close(self) -> None: ...


class CliRunner(Protocol):
    def run(self, profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext) -> CliOutcome: ...


class SessionStore(Protocol):
    def get(self, name: SessionName, cli: str) -> NativeHandle | None: ...

    def save(self, name: SessionName, handle: NativeHandle) -> None: ...


class GitService(Protocol):
    def open(self, cwd: Path, options: GitOptions) -> AbstractContextManager[Path]: ...


class DockerService(Protocol):
    def configure(self, context: RunContext) -> RunContext: ...


class CliAgentClient:
    """Core agent bound to one CLI profile; decides start or resume, then delegates to its runner."""

    def __init__(
        self,
        runner: CliRunner,
        profile: AgentProfile,
        defaults: RunContext | None = None,
        *,
        store: SessionStore | None = None,
        session: SessionName | None = None,
    ) -> None:
        if session is not None and store is None:
            raise ValueError("session requires with_session()")
        self._runner = runner
        self._profile = profile
        self._defaults = defaults or RunContext()
        self._store = store
        self._session = session or (SessionName.new() if store is not None else None)

    def run(self, request: AgentRequest, context: RunContext | None = None) -> AgentResult:
        turn = self._turn()
        outcome = self._runner.run(self._profile, request, turn, context or self._defaults)
        if self._store is not None:
            self._store.save(turn.name, outcome.handle)
        return AgentResult(outcome.output, turn.name, outcome.exit_code)

    def _turn(self) -> Turn:
        if self._store is None or self._session is None:
            return Start(SessionName.new())
        cli = self._profile.cli
        handle = self._store.get(self._session, cli.name)
        if handle is not None:
            if handle.cli != cli.name:
                raise SessionCliMismatch(f"handle for {handle.cli} found under {cli.name}")
            return Resume(self._session, handle)
        new_handle = cli.handle_for_new(self._session)
        if new_handle is not None:
            # Saved before the run so a retry resumes instead of colliding on the name.
            self._store.save(self._session, new_handle)
        return Start(self._session)

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

    def __init__(
        self,
        path: Path,
        build: Callable[[AgentProfile, RunContext, SessionName | None], AgentClient],
        defaults: RunContext,
        session: SessionName | None,
    ) -> None:
        self.path = path
        self.session = session
        self._build = build
        self._defaults = replace(defaults, agent=replace(defaults.agent, cwd=path))
        self._closed = False

    def agent(self, profile: AgentProfile | None = None, session: SessionName | None = None) -> AgentClient:
        if self._closed:
            raise RuntimeError("worktree closed")
        if session is not None and self.session is None:
            raise ValueError("session requires with_session()")
        return self._build(profile or DEFAULT, self._defaults, session or self.session)


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

    def with_git(self, options: GitOptions | None = None) -> AgentBuilder:
        self._use_git = True
        self._git_options = options
        return self

    def with_docker(self) -> AgentBuilder:
        self._use_docker = True
        return self

    def with_session(self) -> AgentBuilder:
        """Continue sessions across runs; without it every run is stateless."""
        self._use_session = True
        return self

    def with_agent_cli_hooks(self, *hooks: AgentCliHook | str) -> AgentBuilder:
        """Observe-only native CLI hooks for every run; a str runs at session start and end."""
        if not hooks:
            raise ValueError("with_agent_cli_hooks() needs at least one hook")
        resolved = tuple(
            hook
            for item in hooks
            for hook in (
                (SessionStartAgentCliHook(item), SessionEndAgentCliHook(item)) if isinstance(item, str) else (item,)
            )
        )
        self._defaults = replace(self._defaults, agent_cli_hooks=resolved)
        return self

    def _require_session_enabled(self, session: SessionName | None) -> None:
        if session is not None and not self._use_session:
            raise ValueError("session requires with_session()")

    def _validate_agent_cli_hooks(self, profile: AgentProfile, defaults: RunContext) -> None:
        requested = {hook.point for hook in defaults.agent_cli_hooks}
        missing = requested - profile.cli.hook_points
        if missing:
            names = ", ".join(sorted(point.value for point in missing))
            raise UnsupportedAgentCliHookPoint(f"{profile.cli.name} cannot fire {names}")

    def _stack(self, profile: AgentProfile, defaults: RunContext, session: SessionName | None) -> AgentClient:
        self._validate_agent_cli_hooks(profile, defaults)
        store = self._sessions if self._use_session else None
        client: AgentClient = CliAgentClient(self._runner, profile, defaults, store=store, session=session)
        if self._use_docker:
            client = DockerAgent(client, self._docker, defaults)
        return client

    @contextmanager
    def open(self, session: SessionName | None = None) -> Iterator[Worktree]:
        """Run the git strategy once and share the worktree between clients of any CLI."""
        self._require_session_enabled(session)
        shared = (session or SessionName.new()) if self._use_session else None
        worktree: Worktree | None = None
        try:
            if self._use_git:
                with self._git.open(self._defaults.agent.cwd, self._git_options or GitOptions()) as path:
                    worktree = Worktree(path, self._stack, self._defaults, shared)
                    yield worktree
            else:
                worktree = Worktree(self._defaults.agent.cwd, self._stack, self._defaults, shared)
                yield worktree
        finally:
            if worktree is not None:
                worktree._closed = True

    def create(self, profile: AgentProfile | None = None, session: SessionName | None = None) -> AgentClient:
        """One-shot client: a separate git lifecycle per run()."""
        self._require_session_enabled(session)
        client = self._stack(profile or DEFAULT, self._defaults, session)
        if self._use_git:
            client = GitAgent(client, self._git, self._git_options, self._defaults)
        return client


# --- Stub adapters to demonstrate dependency registration ---


class MemorySessionStore:
    """Process-local session store; sessions are not persisted across runs of the process."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[SessionName, str], NativeHandle] = {}

    def get(self, name: SessionName, cli: str) -> NativeHandle | None:
        return self._sessions.get((name, cli))

    def save(self, name: SessionName, handle: NativeHandle) -> None:
        self._sessions[(name, handle.cli)] = handle


class CopilotCli:
    """Copilot CLI adapter."""

    name = "copilot"
    hook_points = frozenset(AgentCliHookPoint)
    _EVENTS = {
        AgentCliHookPoint.SESSION_START: "sessionStart",
        AgentCliHookPoint.SESSION_END: "sessionEnd",
        AgentCliHookPoint.AGENT_STOP: "agentStop",
        AgentCliHookPoint.PROMPT_SUBMITTED: "userPromptSubmitted",
        AgentCliHookPoint.PRE_TOOL: "preToolUse",
        AgentCliHookPoint.POST_TOOL: "postToolUse",
        AgentCliHookPoint.SUBAGENT_START: "subagentStart",
        AgentCliHookPoint.SUBAGENT_STOP: "subagentStop",
        AgentCliHookPoint.PRE_COMPACT: "preCompact",
    }

    def __init__(self, args: tuple[str, ...] = ("--allow-all-tools",)) -> None:
        self._args = args

    def native_point(self, point: AgentCliHookPoint) -> str:
        return self._EVENTS[point]

    def hook_wiring(self, hooks: tuple[AgentCliHook, ...], turn: Turn, workdir: Path) -> AgentCliHookWiring:
        events: dict[str, list[dict[str, object]]] = {}
        for point, hook in _ordered(hooks):
            events.setdefault(self._EVENTS[point], []).append(
                {"type": "command", "bash": _shim_command(self.name, point, turn, hook), "timeoutSec": hook.timeout_sec}
            )
        digest = hashlib.sha1(turn.name.value.encode()).hexdigest()[:8]
        content = json.dumps({"version": 1, "hooks": events}, indent=2)
        return AgentCliHookWiring(
            files={Path(f".github/hooks/loop-{digest}.json"): content},
            git_excludes=(".github/hooks/loop-*.json",),
            # Without it `copilot -p` silently skips repo hook files in an untrusted worktree.
            env={"GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS": "true"},
        )

    def handle_for_new(self, name: SessionName) -> NativeHandle:
        return NativeHandle(self.name, name.value)

    def command(
        self, request: AgentRequest, profile: AgentProfile, turn: Turn, context: RunContext
    ) -> list[str]:
        dirs = [part for directory in context.agent.add_dirs for part in ("--add-dir", str(directory))]
        settings = {"--model": profile.model, "--reasoning-effort": profile.reasoning_effort, "--context": profile.context}
        run_args = [part for flag, value in settings.items() if value is not None for part in (flag, value)]
        session_args = ["--name", turn.name.value] if isinstance(turn, Start) else [f"--resume={turn.handle.value}"]
        return ["copilot", "-p", request.prompt, *session_args, *self._args, *run_args, *profile.args, *dirs]

    def parse(self, stdout: str, turn: Turn, exit_code: int) -> CliOutcome:
        handle = turn.handle if isinstance(turn, Resume) else self.handle_for_new(turn.name)
        return CliOutcome(stdout, handle, exit_code)


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
    _EVENTS = {
        AgentCliHookPoint.SESSION_START: "SessionStart",
        AgentCliHookPoint.SESSION_END: "SessionEnd",
        AgentCliHookPoint.AGENT_STOP: "Stop",
        AgentCliHookPoint.PROMPT_SUBMITTED: "UserPromptSubmit",
        AgentCliHookPoint.PRE_TOOL: "PreToolUse",
        AgentCliHookPoint.POST_TOOL: "PostToolUse",
        AgentCliHookPoint.SUBAGENT_START: "SubagentStart",
        AgentCliHookPoint.SUBAGENT_STOP: "SubagentStop",
        AgentCliHookPoint.PRE_COMPACT: "PreCompact",
    }
    _SESSION_END_MAX_TIMEOUT = 3

    def __init__(self, args: tuple[str, ...] = ("--sandbox", "workspace-write"), *, hook_trust_bypass: bool = False) -> None:
        self._args = args
        self._hook_trust_bypass = hook_trust_bypass
        # Without the bypass Codex silently skips the hooks, so none are claimed.
        self.hook_points = frozenset(AgentCliHookPoint) if hook_trust_bypass else frozenset()

    def native_point(self, point: AgentCliHookPoint) -> str:
        return self._EVENTS[point]

    def hook_wiring(self, hooks: tuple[AgentCliHook, ...], turn: Turn, workdir: Path) -> AgentCliHookWiring:
        events: dict[str, list[str]] = {}
        for point, hook in _ordered(hooks):
            timeout = hook.timeout_sec
            if point is AgentCliHookPoint.SESSION_END:
                timeout = min(timeout, self._SESSION_END_MAX_TIMEOUT)
            command = json.dumps(_shim_command(self.name, point, turn, hook))
            matcher = 'matcher="startup|resume",' if point is AgentCliHookPoint.SESSION_START else ""
            events.setdefault(self._EVENTS[point], []).append(
                f'{{{matcher}hooks=[{{type="command",command={command},timeout={timeout}}}]}}'
            )
        args: list[str] = []
        for event, entries in events.items():
            args += ["-c", f"hooks.{event}=[{','.join(entries)}]"]
        return AgentCliHookWiring(args=(*args, "--dangerously-bypass-hook-trust"))

    def handle_for_new(self, name: SessionName) -> None:
        return None

    def command(
        self, request: AgentRequest, profile: AgentProfile, turn: Turn, context: RunContext
    ) -> list[str]:
        dirs = [part for directory in context.agent.add_dirs for part in ("--add-dir", str(directory))]
        run_args: list[str] = []
        if profile.model is not None:
            run_args += ["--model", profile.model]
        if profile.reasoning_effort is not None:
            run_args += ["-c", f"model_reasoning_effort={profile.reasoning_effort}"]
        head = ["codex", "exec", "--json", *self._args, *run_args, *profile.args, *dirs]
        if context.agent_cli_hooks:
            head += self.hook_wiring(context.agent_cli_hooks, turn, context.agent.cwd).args
        if isinstance(turn, Resume):
            return [*head[:2], "resume", turn.handle.value, *head[2:], request.prompt]
        return [*head, request.prompt]

    def parse(self, stdout: str, turn: Turn, exit_code: int) -> CliOutcome:
        thread_id, message = _parse_codex_events(stdout)
        if thread_id is not None:
            return CliOutcome(message, NativeHandle(self.name, thread_id), exit_code)
        if isinstance(turn, Resume):
            return CliOutcome(message, turn.handle, exit_code)
        raise SessionHandleMissing("codex output had no thread.started event")


copilot = CopilotCli()
codex = CodexCli()
DEFAULT = AgentProfile(copilot)


class ProcessCliRunner:
    """Runs any AgentCli as a process in the agent cwd; `run` is injectable for testing."""

    def __init__(self, *, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self._run = run

    def run(self, profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext) -> CliOutcome:
        argv = profile.cli.command(request, profile, turn, context)
        wiring = (
            profile.cli.hook_wiring(context.agent_cli_hooks, turn, context.agent.cwd)
            if context.agent_cli_hooks
            else AgentCliHookWiring()
        )
        extra = {"env": {**os.environ, **wiring.env}} if wiring.env else {}
        with self._hook_files(wiring, context.agent.cwd):
            result = self._run(argv, cwd=context.agent.cwd, check=True, capture_output=True, text=True, **extra)
        return profile.cli.parse(result.stdout, turn, result.returncode)

    @contextmanager
    def _hook_files(self, wiring: AgentCliHookWiring, cwd: Path) -> Iterator[None]:
        written: list[Path] = []
        try:
            for relative, content in wiring.files.items():
                path = cwd / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                written.append(path)
            if wiring.git_excludes:
                self._exclude(cwd, wiring.git_excludes)
            yield
        finally:
            for path in written:
                path.unlink(missing_ok=True)

    def _exclude(self, cwd: Path, patterns: tuple[str, ...]) -> None:
        found = self._run(
            ["git", "-C", str(cwd), "rev-parse", "--git-path", "info/exclude"],
            check=False, capture_output=True, text=True,
        )
        if found.returncode != 0 or not found.stdout.strip():
            return
        exclude = cwd / found.stdout.strip()
        existing = exclude.read_text().splitlines() if exclude.exists() else []
        missing = [pattern for pattern in patterns if pattern not in existing]
        if missing:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a") as handle:
                handle.writelines(f"{pattern}\n" for pattern in missing)


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

    def remove_worktree(self, repository: Path, target: Path, *, force: bool = False) -> None:
        """Detach the worktree at `target`; git refuses when it has uncommitted changes unless `force`. The branch is kept."""
        flags = ["--force"] if force else []
        self._git(repository, "worktree", "remove", *flags, str(target))

    def run_hook(self, hook: LoopHook, cwd: Path, repository: Path, worktree: Path) -> None:
        try:
            result = self._run(
                hook.command,
                shell=True,
                cwd=cwd,
                timeout=hook.timeout_sec,
                env={
                    **os.environ,
                    "LOOP_REPOSITORY": str(repository),
                    "LOOP_WORKTREE": str(worktree),
                    "LOOP_HOOK_POINT": hook.point.value,
                },
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise LoopHookError(hook.point, hook.command, f"timed out after {hook.timeout_sec}s") from None
        if result.returncode != 0:
            raise LoopHookError(hook.point, hook.command, (result.stdout or "") + (result.stderr or ""))

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
        for hook in options.hooks_at(LoopHookPoint.WORKTREE_READY):
            git.run_hook(hook, target, repository, target)
    except BaseException as error:
        try:
            git.remove_worktree(repository, target, force=True)
        except Exception as removal:
            error.add_note(f"forced worktree removal also failed: {removal}")
        raise
    try:
        yield target
        for hook in options.hooks_at(LoopHookPoint.WORKTREE_REMOVING):
            git.run_hook(hook, target, repository, target)
    finally:
        git.remove_worktree(repository, target)


class GitRuntime:
    """Delegates to the options' strategy and yields the directory the agent works in."""

    def __init__(self, git: GitCli) -> None:
        self._git = git

    @contextmanager
    def open(self, cwd: Path, options: GitOptions) -> Iterator[Path]:
        repository = (cwd / options.repository_path) if options.repository_path else cwd
        with options.strategy.open(self._git, cwd, repository, options) as target:
            yield target
        # Reached only when the strategy exited successfully.
        for hook in options.hooks_at(LoopHookPoint.RUN_FINISHED):
            self._git.run_hook(hook, repository, repository, target)


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

    def run_hook(self, hook: LoopHook, cwd: Path, repository: Path, worktree: Path) -> None:
        _log(f"loop-hook {hook.point.value} cwd={cwd} timeout={hook.timeout_sec}: {hook.command}")


class LoggingRunner:
    """Dry-run CliRunner: logs the command instead of running it."""

    def run(self, profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext) -> CliOutcome:
        command = profile.cli.command(request, profile, turn, context)
        output = f"cwd={context.agent.cwd} cmd={' '.join(command)}"
        _log(output)
        self._log_hooks(profile, turn, context)
        return CliOutcome(output, self._handle(profile.cli, turn), 0)

    def _log_hooks(self, profile: AgentProfile, turn: Turn, context: RunContext) -> None:
        if not context.agent_cli_hooks:
            return
        cli = profile.cli
        for point, hook in _ordered(context.agent_cli_hooks):
            _log(f"hook {cli.name} {point.value} -> {cli.native_point(point)}: {hook.command}")
        wiring = cli.hook_wiring(context.agent_cli_hooks, turn, context.agent.cwd)
        for path in wiring.files:
            _log(f"hook file {context.agent.cwd / path}")
        if wiring.args:
            _log(f"hook args {' '.join(wiring.args)}")
        if wiring.env:
            _log(f"hook env {dict(wiring.env)}")

    def _handle(self, cli: AgentCli, turn: Turn) -> NativeHandle:
        if isinstance(turn, Resume):
            return turn.handle
        return cli.handle_for_new(turn.name) or NativeHandle(cli.name, f"dry-{uuid.uuid4().hex}")


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


def repo_agent(repo: str, branch: str | None = None, session: SessionName | None = None) -> AgentClient:
    """One worktree-isolated agent for workspace/<repo>, with worktrees in workspace/<repo>.worktrees; a `session` continues across runs."""
    workspace = Path("workspace")
    options = GitOptions(
        root_path=workspace / f"{repo}.worktrees",
        repository_path=workspace / repo,
        strategy=BranchStrategy(branch) if branch else MergeToHeadStrategy(),
    )
    builder = Agent().with_git(options).with_docker()
    return builder.with_session().create(session=session) if session else builder.create()


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
        DEVELOPER = AgentProfile(CodexCli(hook_trust_bypass=True), "gpt-5-codex", "high")
        REVIEWER = AgentProfile(copilot, "claude-sonnet-4.5")
        # Shared worktree: one git lifecycle, several CLIs; one named session, kept per CLI.
        _loop_hooks = (
            WorktreeReadyLoopHook('cp "$LOOP_REPOSITORY/.env" .env'),
            WorktreeReadyLoopHook("npm ci", timeout_sec=600),
        )
        _shared = Agent(_DRY).with_git(
            GitOptions(_root, _repo, BranchStrategy("loop/ticket-123", "main"), loop_hooks=_loop_hooks)
        ).with_session()
        _shared.with_agent_cli_hooks(
            SessionStartAgentCliHook("./scripts/session-start.sh"),
            SessionEndAgentCliHook("notify-send", timeout_sec=3),
        )
        with _shared.open(session=SessionName("ticket-123")) as _wt:
            _plan = _wt.agent(PLANNER).run(AgentRequest("Plan ticket #123 in repo1"))
            _developer = _wt.agent(DEVELOPER)
            _developer.run(AgentRequest(f"Implement this plan:\n{_plan.output}"))
            _developer.run(AgentRequest("Fix failing tests"))  # resumes the Codex session
            # A fresh session gives an unbiased review.
            _wt.agent(REVIEWER, session=SessionName.new()).run(AgentRequest("Review the diff against the plan"))
        # Without with_session(), each run starts under a new `loop-<hex>` name.
        _fresh = Agent(_DRY).with_git(GitOptions(_root, _repo, BranchStrategy("loop/ticket-123", "main"))).create()
        _fresh.run(AgentRequest("Plan ticket #123 in repo1"))
        _fresh.run(AgentRequest("Implement ticket #123 in repo1"))
        sys.exit()
    # print(repo_agent("repo1", "loop/ticket-123").run("Implement ticket #123 in repo1"))
    # print(repo_agent("repo2", "loop/ticket-123").run("Implement ticket #123 in repo2"))
    # Named session: the second run resumes the first (the client is reused).
    # _agent = repo_agent("repo1", "loop/ticket-123", session=SessionName("ticket-123"))
    # _agent.run(AgentRequest("Plan ticket #123 in repo1"))
    # _agent.run(AgentRequest("Implement the plan"))

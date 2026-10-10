from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from .agents import AgentClient, CliAgentClient, DockerAgent, GitAgent
from .clis import DEFAULT, AgentProfile, CliRunner
from .docker import DockerService
from .git import GitOptions, GitService
from .hooks import (
    AgentCliHook,
    SessionEndAgentCliHook,
    SessionStartAgentCliHook,
    UnsupportedAgentCliHookPoint,
)
from .run import AgentContext, RunContext
from .sessions import MemorySessionStore, SessionName, SessionStore


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

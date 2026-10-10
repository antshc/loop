from __future__ import annotations

from dataclasses import replace

from ..docker import DockerService
from ..git import GitOptions, GitService
from ..run import AgentRequest, AgentResult, RunContext
from .client import AgentClient


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

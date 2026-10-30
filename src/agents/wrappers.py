from __future__ import annotations

from dataclasses import replace

from ..docker import DockerService
from ..git import GitOptions, GitService
from ..run import AgentContext, AgentRequest, AgentResult
from .client import AgentClient


class AgentWrapper:
    """Composes an AgentClient and delegates common lifecycle operations."""

    def __init__(self, inner: AgentClient, defaults: AgentContext | None = None) -> None:
        self._inner = inner
        self._defaults = defaults or AgentContext()

    def close(self) -> None:
        self._inner.close()


class GitAgent(AgentWrapper):
    def __init__(
        self,
        inner: AgentClient,
        git: GitService,
        options: GitOptions | None = None,
        defaults: AgentContext | None = None,
    ) -> None:
        super().__init__(inner, defaults)
        self._git = git
        self._options = options or GitOptions()

    def run(self, request: AgentRequest, context: AgentContext | None = None) -> AgentResult:
        original = context or self._defaults
        # The strategy's git lifecycle surrounds the entire delegated execution.
        with self._git.open(original.cwd, self._options) as workdir:
            # The worktree is inside cwd, so the CLI already has access to it.
            return self._inner.run(request, replace(original, cwd=workdir))


class DockerAgent(AgentWrapper):
    def __init__(
        self,
        inner: AgentClient,
        docker: DockerService,
        defaults: AgentContext | None = None,
    ) -> None:
        super().__init__(inner, defaults)
        self._docker = docker

    def run(self, request: AgentRequest, context: AgentContext | None = None) -> AgentResult:
        configured = self._docker.configure(context or self._defaults)
        return self._inner.run(request, configured)

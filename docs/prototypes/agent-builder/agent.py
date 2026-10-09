"""Standalone contract prototype: Agent builder with composed execution wrappers.

No real Git worktree, Docker container, or Copilot process is started here.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterator, Protocol, Self


@dataclass(frozen=True)
class RunContext:
    cwd: Path = field(default_factory=Path.cwd)
    docker_image: str | None = None


class AgentClient(Protocol):
    """Stable public interface, regardless of enabled wrappers."""

    def run(self, prompt: str, context: RunContext | None = None) -> str: ...

    def close(self) -> None: ...


class CliRunner(Protocol):
    def run(self, prompt: str, context: RunContext) -> str: ...


class WorktreeService(Protocol):
    def open(self, cwd: Path) -> AbstractContextManager[Path]: ...


class DockerService(Protocol):
    def configure(self, context: RunContext) -> RunContext: ...


class CopilotAgentClient:
    """Core agent; delegates execution to its runner."""

    def __init__(self, runner: CliRunner) -> None:
        self._runner = runner

    def run(self, prompt: str, context: RunContext | None = None) -> str:
        return self._runner.run(prompt, context or RunContext())

    def close(self) -> None:
        pass


class AgentWrapper:
    """Composes an AgentClient and delegates common lifecycle operations."""

    def __init__(self, inner: AgentClient) -> None:
        self._inner = inner

    def close(self) -> None:
        self._inner.close()


class WorktreeAgent(AgentWrapper):
    def __init__(self, inner: AgentClient, worktrees: WorktreeService) -> None:
        super().__init__(inner)
        self._worktrees = worktrees

    def run(self, prompt: str, context: RunContext | None = None) -> str:
        original = context or RunContext()
        # The worktree lifecycle surrounds the entire delegated execution.
        with self._worktrees.open(original.cwd) as workspace:
            return self._inner.run(prompt, replace(original, cwd=workspace))


class DockerAgent(AgentWrapper):
    def __init__(self, inner: AgentClient, docker: DockerService) -> None:
        super().__init__(inner)
        self._docker = docker

    def run(self, prompt: str, context: RunContext | None = None) -> str:
        configured = self._docker.configure(context or RunContext())
        return self._inner.run(prompt, configured)


class AgentBuilder:
    def __init__(
        self,
        runner: CliRunner,
        worktrees: WorktreeService,
        docker: DockerService,
    ) -> None:
        self._runner = runner
        self._worktrees = worktrees
        self._docker = docker
        self._use_worktrees = False
        self._use_docker = False

    def WithWorktrees(self) -> Self:
        self._use_worktrees = True
        return self

    def withdocker(self) -> Self:
        self._use_docker = True
        return self

    def create(self) -> AgentClient:
        client: AgentClient = CopilotAgentClient(self._runner)
        if self._use_docker:
            client = DockerAgent(client, self._docker)
        if self._use_worktrees:
            client = WorktreeAgent(client, self._worktrees)
        return client


# --- Stub adapters to demonstrate dependency registration ---


class CopilotCli:
    def run(self, prompt: str, context: RunContext) -> str:
        # Demonstration only: a real adapter would invoke the Copilot CLI.
        mode = f"docker:{context.docker_image}" if context.docker_image else "local"
        return f"[{mode}] cwd={context.cwd} prompt={prompt}"


class GitWorktrees:
    @contextmanager
    def open(self, cwd: Path) -> Iterator[Path]:
        # Demonstration only: no git worktree is created or removed.
        try:
            yield cwd / ".worktrees" / "task"
        finally:
            pass


class DockerRuntime:
    def configure(self, context: RunContext) -> RunContext:
        # Demonstration only: records intent; runner does not launch Docker.
        return replace(context, docker_image="agent:latest")


def Agent() -> AgentBuilder:
    """Composition root: register private default dependencies and return builder."""
    return AgentBuilder(
        runner=CopilotCli(),
        worktrees=GitWorktrees(),
        docker=DockerRuntime(),
    )


if __name__ == "__main__":
    print(Agent().create().run("Implement ticket #123"))
    print(Agent().WithWorktrees().withdocker().create().run("Implement ticket #123"))

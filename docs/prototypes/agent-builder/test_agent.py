"""Run with: python -m unittest discover -s docs/prototypes/agent-builder -p 'test_*.py'"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from agent import (
    Agent,
    AgentBuilder,
    CopilotAgentClient,
    DockerAgent,
    RunContext,
    WorktreeAgent,
)


class RecordingRunner:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.contexts: list[RunContext] = []

    def run(self, prompt: str, context: RunContext) -> str:
        self.events.append("cli.run")
        self.contexts.append(context)
        if self.fail:
            raise RuntimeError("runner failed")
        return f"done:{prompt}"


class RecordingWorktrees:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    @contextmanager
    def open(self, cwd: Path) -> Iterator[Path]:
        self.events.append("worktree.enter")
        try:
            yield cwd / "worktree"
        finally:
            self.events.append("worktree.exit")


class RecordingDocker:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def configure(self, context: RunContext) -> RunContext:
        from dataclasses import replace

        self.events.append("docker.configure")
        return replace(context, docker_image="test-image")


class ClosingClient:
    def __init__(self) -> None:
        self.close_count = 0

    def run(self, prompt: str, context: RunContext | None = None) -> str:
        return prompt

    def close(self) -> None:
        self.close_count += 1


class AgentBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[str] = []
        self.runner = RecordingRunner(self.events)
        self.worktrees = RecordingWorktrees(self.events)
        self.docker = RecordingDocker(self.events)

    def builder(self) -> AgentBuilder:
        return AgentBuilder(self.runner, self.worktrees, self.docker)

    def test_default_client_has_no_wrappers(self) -> None:
        client = Agent().create()
        self.assertIsInstance(client, CopilotAgentClient)
        self.assertIn("[local]", client.run("hello"))

    def test_one_chain_creates_worktree_outer_and_docker_inner(self) -> None:
        builder = self.builder()
        self.assertIs(builder.with_worktrees(), builder)
        self.assertIs(builder.with_docker(), builder)
        client = builder.create()
        self.assertIsInstance(client, WorktreeAgent)
        self.assertIsInstance(client._inner, DockerAgent)
        self.assertIsInstance(client._inner._inner, CopilotAgentClient)
        self.assertEqual(client.run("hello", RunContext(cwd=Path("/repo"))), "done:hello")
        self.assertEqual(
            self.events,
            ["worktree.enter", "docker.configure", "cli.run", "worktree.exit"],
        )
        self.assertEqual(self.runner.contexts[0].cwd, Path("/repo/worktree"))
        self.assertEqual(self.runner.contexts[0].docker_image, "test-image")

    def test_worktree_is_released_on_failure(self) -> None:
        self.runner.fail = True
        with self.assertRaisesRegex(RuntimeError, "runner failed"):
            self.builder().with_worktrees().create().run("broken")
        self.assertEqual(self.events, ["worktree.enter", "cli.run", "worktree.exit"])

    def test_docker_only_keeps_original_workspace(self) -> None:
        self.builder().with_docker().create().run("hello", RunContext(cwd=Path("/repo")))
        self.assertEqual(self.events, ["docker.configure", "cli.run"])
        self.assertEqual(self.runner.contexts[0].cwd, Path("/repo"))

    def test_close_is_delegated(self) -> None:
        inner = ClosingClient()
        wrapped = WorktreeAgent(DockerAgent(inner, self.docker), self.worktrees)
        wrapped.close()
        self.assertEqual(inner.close_count, 1)


if __name__ == "__main__":
    unittest.main()

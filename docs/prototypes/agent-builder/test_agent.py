"""Run with: python -m unittest discover -s docs/prototypes/agent-builder -p 'test_*.py'"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from agent import (
    Agent,
    AgentBuilder,
    AgentContext,
    CopilotAgentClient,
    DockerAgent,
    GitCli,
    RunContext,
    WorktreeAgent,
    WorktreesOptions,
    WorktreesRuntime,
)


def repo_context() -> RunContext:
    return RunContext(AgentContext(cwd=Path("/repo")))


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
    def open(self, cwd: Path, options: WorktreesOptions) -> Iterator[Path]:
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
        return replace(context, agent=replace(context.agent, docker_image="test-image"))


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
        self.assertIn("--allow-all-tools", client.run("hello"))

    def test_one_chain_creates_worktree_outer_and_docker_inner(self) -> None:
        builder = self.builder()
        self.assertIs(builder.with_worktrees(), builder)
        self.assertIs(builder.with_docker(), builder)
        client = builder.create()
        self.assertIsInstance(client, WorktreeAgent)
        self.assertIsInstance(client._inner, DockerAgent)
        self.assertIsInstance(client._inner._inner, CopilotAgentClient)
        self.assertEqual(client.run("hello", repo_context()), "done:hello")
        self.assertEqual(
            self.events,
            ["worktree.enter", "docker.configure", "cli.run", "worktree.exit"],
        )
        self.assertEqual(self.runner.contexts[0].agent.cwd, Path("/repo"))
        self.assertEqual(self.runner.contexts[0].agent.add_dirs, (Path("/repo/worktree"),))
        self.assertEqual(self.runner.contexts[0].agent.docker_image, "test-image")

    def test_worktree_is_released_on_failure(self) -> None:
        self.runner.fail = True
        with self.assertRaisesRegex(RuntimeError, "runner failed"):
            self.builder().with_worktrees().create().run("broken")
        self.assertEqual(self.events, ["worktree.enter", "cli.run", "worktree.exit"])

    def test_docker_only_keeps_original_workspace(self) -> None:
        self.builder().with_docker().create().run("hello", repo_context())
        self.assertEqual(self.events, ["docker.configure", "cli.run"])
        self.assertEqual(self.runner.contexts[0].agent.cwd, Path("/repo"))

    def test_close_is_delegated(self) -> None:
        inner = ClosingClient()
        wrapped = WorktreeAgent(DockerAgent(inner, self.docker), self.worktrees)
        wrapped.close()
        self.assertEqual(inner.close_count, 1)


class FakeProcess:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


class GitFixture:
    """Records git invocations; `missing` ref fragments make show-ref fail."""

    def __init__(self, missing: tuple[str, ...] = ()) -> None:
        self.missing = missing
        self.commands: list[list[str]] = []
        self.repositories: set[str] = set()

    def __call__(self, command: list[str], **_: object) -> FakeProcess:
        self.repositories.add(command[2])
        self.commands.append(command[3:])
        failed = command[3] == "show-ref" and any(ref in command[-1] for ref in self.missing)
        return FakeProcess(1 if failed else 0)


class GitCliTests(unittest.TestCase):
    def test_creates_new_branch_from_base_when_nothing_exists(self) -> None:
        git = GitFixture(missing=("refs/heads/", "refs/remotes/"))
        GitCli(run=git).create_worktree(Path("/repo"), Path("/repo/.wt/loop/a"), "loop/a")
        self.assertEqual(git.commands[0], ["fetch", "--all", "--prune"])
        self.assertEqual(git.commands[1], ["check-ref-format", "--branch", "loop/a"])
        self.assertEqual(git.commands[-2], ["branch", "loop/a", "origin/main"])
        self.assertEqual(git.commands[-1], ["worktree", "add", "/repo/.wt/loop/a", "loop/a"])

    def test_moves_existing_branch_to_remote_branch(self) -> None:
        git = GitFixture()
        GitCli(run=git).create_worktree(Path("/repo"), Path("/repo/.wt/loop/a"), "loop/a")
        self.assertEqual(git.commands[-2], ["branch", "-f", "loop/a", "origin/loop/a"])


class WorktreesRuntimeTests(unittest.TestCase):
    def test_target_is_root_path_slash_branch(self) -> None:
        git = GitFixture(missing=("refs/",))
        runtime = WorktreesRuntime(GitCli(run=git))
        with runtime.open(Path("/repo"), WorktreesOptions(Path("wt"), "loop/a")) as target:
            self.assertEqual(target, Path("/repo/wt/loop/a"))
        self.assertEqual(git.repositories, {"/repo"})

    def test_unset_branch_is_generated_as_feat_hex(self) -> None:
        git = GitFixture(missing=("refs/",))
        runtime = WorktreesRuntime(GitCli(run=git))
        with runtime.open(Path("/repo"), WorktreesOptions(Path("wt"))) as target:
            self.assertRegex(target.name, r"^feat_[0-9a-f]{8}$")
        self.assertEqual(git.commands[-2][-1], target.name)

    def test_worktree_is_removed_on_exit_even_on_error(self) -> None:
        git = GitFixture(missing=("refs/",))
        runtime = WorktreesRuntime(GitCli(run=git))
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with runtime.open(Path("/repo"), WorktreesOptions(Path("wt"), "loop/a")):
                raise RuntimeError("boom")
        self.assertEqual(git.commands[-1], ["worktree", "remove", "/repo/wt/loop/a"])

    def test_repository_path_selects_git_c_target(self) -> None:
        git = GitFixture(missing=("refs/",))
        runtime = WorktreesRuntime(GitCli(run=git))
        options = WorktreesOptions(Path("wt"), "loop/a", Path("services/api"))
        with runtime.open(Path("/repo"), options):
            pass
        self.assertEqual(git.repositories, {"/repo/services/api"})

    def test_root_outside_cwd_is_rejected(self) -> None:
        runtime = WorktreesRuntime(GitCli(run=GitFixture()))
        with self.assertRaisesRegex(ValueError, "must be inside"):
            with runtime.open(Path("/repo"), WorktreesOptions(Path("../x"))):
                pass


if __name__ == "__main__":
    unittest.main()

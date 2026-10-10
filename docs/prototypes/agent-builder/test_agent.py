"""Run with: python -m unittest discover -s docs/prototypes/agent-builder -p 'test_*.py'"""

from __future__ import annotations

import unittest
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from typing import Iterator

from agent import (
    Agent,
    AgentBuilder,
    AgentCli,
    AgentContext,
    AgentOptions,
    AgentProfile,
    AgentRequest,
    AgentResult,
    BranchStrategy,
    CliAgentClient,
    CodexCli,
    CopilotCli,
    DockerAgent,
    GitAgent,
    GitCli,
    GitOptions,
    GitRuntime,
    HeadStrategy,
    MergeToHeadStrategy,
    ProcessCliRunner,
    RunContext,
    SessionAgent,
    _parse_codex_events,
    codex,
    copilot,
)


def repo_context() -> RunContext:
    return RunContext(AgentContext(cwd=Path("/repo")))


class RecordingRunner:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.contexts: list[RunContext] = []
        self.requests: list[AgentRequest] = []
        self.profiles: list[AgentProfile] = []

    def run(self, profile: AgentProfile, request: AgentRequest, context: RunContext) -> AgentResult:
        self.events.append("cli.run")
        self.profiles.append(profile)
        self.contexts.append(context)
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("runner failed")
        return AgentResult(
            f"done:{request.prompt}", request.session_id or f"{profile.cli.name}{len(self.requests)}", 0
        )


class RecordingGit:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    @contextmanager
    def open(self, cwd: Path, options: GitOptions) -> Iterator[Path]:
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

    def run(self, request: AgentRequest, context: RunContext | None = None) -> AgentResult:
        return AgentResult(request.prompt, "s", 0)

    def close(self) -> None:
        self.close_count += 1


class AgentBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[str] = []
        self.runner = RecordingRunner(self.events)
        self.git = RecordingGit(self.events)
        self.docker = RecordingDocker(self.events)

    def builder(self) -> AgentBuilder:
        return AgentBuilder(self.runner, self.git, self.docker)

    def test_default_client_has_no_wrappers(self) -> None:
        client = Agent().create()
        self.assertIsInstance(client, CliAgentClient)

    def test_run_returns_result_with_session_id(self) -> None:
        result = self.builder().create().run(AgentRequest("hello"), repo_context())
        self.assertEqual(result, AgentResult("done:hello", "copilot1", 0))

    def test_explicit_session_id_is_forwarded(self) -> None:
        result = self.builder().create().run(AgentRequest("hello", "abc"), repo_context())
        self.assertEqual(self.runner.requests[0].session_id, "abc")
        self.assertEqual(result.session_id, "abc")

    def test_runs_are_stateless_without_session(self) -> None:
        client = self.builder().create()
        client.run(AgentRequest("one"), repo_context())
        client.run(AgentRequest("two"), repo_context())
        self.assertEqual([r.session_id for r in self.runner.requests], [None, None])

    def test_session_is_shared_between_runs_in_one_cwd(self) -> None:
        client = self.builder().with_session().create()
        self.assertIsInstance(client, SessionAgent)
        first = client.run(AgentRequest("one"), repo_context())
        second = client.run(AgentRequest("two"), repo_context())
        self.assertEqual(second.session_id, first.session_id)
        self.assertEqual(self.runner.requests[1].session_id, first.session_id)

    def test_explicit_session_overrides_stored_session(self) -> None:
        client = self.builder().with_session().create()
        client.run(AgentRequest("one"), repo_context())
        result = client.run(AgentRequest("two", "other"), repo_context())
        self.assertEqual(result.session_id, "other")

    def test_sessions_are_unique_per_worktree(self) -> None:
        client = self.builder().with_session().create()
        a = client.run(AgentRequest("one"), RunContext(AgentContext(cwd=Path("/wt/a"))))
        b = client.run(AgentRequest("one"), RunContext(AgentContext(cwd=Path("/wt/b"))))
        again = client.run(AgentRequest("two"), RunContext(AgentContext(cwd=Path("/wt/a"))))
        self.assertNotEqual(a.session_id, b.session_id)
        self.assertEqual(again.session_id, a.session_id)

    def test_session_sees_the_git_worktree(self) -> None:
        client = self.builder().with_git().with_session().create()
        self.assertIsInstance(client, GitAgent)
        self.assertIsInstance(client._inner, SessionAgent)
        client.run(AgentRequest("one"), repo_context())
        second = client.run(AgentRequest("two"), repo_context())
        self.assertEqual(second.session_id, "copilot1")

    def test_one_chain_creates_git_outer_and_docker_inner(self) -> None:
        builder = self.builder()
        self.assertIs(builder.with_git(), builder)
        self.assertIs(builder.with_docker(), builder)
        client = builder.create()
        self.assertIsInstance(client, GitAgent)
        self.assertIsInstance(client._inner, DockerAgent)
        self.assertIsInstance(client._inner._inner, CliAgentClient)
        self.assertEqual(client.run(AgentRequest("hello"), repo_context()).output, "done:hello")
        self.assertEqual(
            self.events,
            ["worktree.enter", "docker.configure", "cli.run", "worktree.exit"],
        )
        self.assertEqual(self.runner.contexts[0].agent.cwd, Path("/repo/worktree"))
        self.assertEqual(self.runner.contexts[0].agent.docker_image, "test-image")

    def test_worktree_is_released_on_failure(self) -> None:
        self.runner.fail = True
        with self.assertRaisesRegex(RuntimeError, "runner failed"):
            self.builder().with_git().create().run(AgentRequest("broken"))
        self.assertEqual(self.events, ["worktree.enter", "cli.run", "worktree.exit"])

    def test_docker_only_keeps_original_workspace(self) -> None:
        self.builder().with_docker().create().run(AgentRequest("hello"), repo_context())
        self.assertEqual(self.events, ["docker.configure", "cli.run"])
        self.assertEqual(self.runner.contexts[0].agent.cwd, Path("/repo"))

    def test_close_is_delegated(self) -> None:
        inner = ClosingClient()
        wrapped = GitAgent(DockerAgent(inner, self.docker), self.git)
        wrapped.close()
        self.assertEqual(inner.close_count, 1)


PLANNER = AgentProfile(copilot, "claude-opus-4.5", "high")
DEVELOPER = AgentProfile(codex, "gpt-5-codex", "high")


class WorktreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[str] = []
        self.runner = RecordingRunner(self.events)
        self.builder = AgentBuilder(self.runner, RecordingGit(self.events), RecordingDocker(self.events)).with_git()

    def test_two_profiles_share_one_worktree(self) -> None:
        with self.builder.open() as wt:
            wt.agent(PLANNER).run(AgentRequest("plan"))
            wt.agent(DEVELOPER).run(AgentRequest("implement"))
        self.assertEqual(self.events, ["worktree.enter", "cli.run", "cli.run", "worktree.exit"])
        self.assertEqual([c.agent.cwd for c in self.runner.contexts], [wt.path, wt.path])
        self.assertEqual([p.cli.name for p in self.runner.profiles], ["copilot", "codex"])

    def test_sessions_are_per_cli_inside_one_worktree(self) -> None:
        with self.builder.with_session().open() as wt:
            wt.agent(DEVELOPER).run(AgentRequest("one"))
            wt.agent(PLANNER).run(AgentRequest("two"))
            wt.agent(DEVELOPER).run(AgentRequest("three"))
            wt.agent(PLANNER).run(AgentRequest("four"))
        ids = [r.session_id for r in self.runner.requests]
        self.assertEqual(ids, [None, None, "codex1", "copilot2"])

    def test_exception_in_block_still_exits_worktree(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with self.builder.open():
                raise RuntimeError("boom")
        self.assertEqual(self.events, ["worktree.enter", "worktree.exit"])

    def test_agent_after_block_raises(self) -> None:
        with self.builder.open() as wt:
            pass
        with self.assertRaisesRegex(RuntimeError, "worktree closed"):
            wt.agent()

    def test_open_without_git_yields_cwd(self) -> None:
        builder = AgentBuilder(self.runner, RecordingGit(self.events), RecordingDocker(self.events), AgentContext(cwd=Path("/repo")))
        with builder.open() as wt:
            self.assertEqual(wt.path, Path("/repo"))
        self.assertEqual(self.events, [])


class UserCli:
    name = "user"

    def new_session_id(self) -> str | None:
        return "fixed"

    def command(self, request: AgentRequest, profile: AgentProfile, session_id: str | None, context: RunContext) -> list[str]:
        return ["user-cli", request.prompt, str(session_id), *profile.args]

    def parse(self, stdout: str, session_id: str | None, exit_code: int) -> AgentResult:
        return AgentResult(stdout.upper(), session_id or "", exit_code)


class CliTests(unittest.TestCase):
    def test_user_defined_cli_runs_through_process_runner(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake(argv: list[str], **kwargs: object) -> object:
            calls.append((argv, kwargs))
            return type("P", (), {"stdout": "out", "returncode": 0})()

        cli: AgentCli = UserCli()
        result = ProcessCliRunner(run=fake).run(AgentProfile(cli, args=("-x",)), AgentRequest("hi"), repo_context())
        self.assertEqual(result, AgentResult("OUT", "fixed", 0))
        self.assertEqual(calls[0][0], ["user-cli", "hi", "fixed", "-x"])
        self.assertEqual(calls[0][1]["cwd"], Path("/repo"))

    def test_copilot_command(self) -> None:
        profile = replace(PLANNER, args=("--x",))
        command = CopilotCli().command(AgentRequest("p"), profile, "sid", repo_context())
        self.assertEqual(
            command,
            ["copilot", "-p", "p", "--resume=sid", "--allow-all-tools", "--model", "claude-opus-4.5",
             "--reasoning-effort", "high", "--x"],
        )

    def test_codex_command_new_and_resume(self) -> None:
        profile = replace(DEVELOPER, args=("--x",))
        new = CodexCli().command(AgentRequest("p"), profile, None, repo_context())
        self.assertEqual(
            new,
            ["codex", "exec", "--json", "--sandbox", "workspace-write", "--model", "gpt-5-codex",
             "-c", "model_reasoning_effort=high", "--x", "p"],
        )
        resumed = CodexCli().command(AgentRequest("p"), profile, "t1", repo_context())
        self.assertEqual(resumed[:4], ["codex", "exec", "resume", "t1"])
        self.assertEqual(resumed[-1], "p")

    def test_parse_codex_events(self) -> None:
        stdout = "\n".join([
            '{"type":"thread.started","thread_id":"t1"}',
            "not json",
            '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"last"}}',
        ])
        self.assertEqual(_parse_codex_events(stdout), ("t1", "last"))
        self.assertEqual(CodexCli().parse(stdout, None, 0), AgentResult("last", "t1", 0))


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
    def test_new_branch_starts_from_base_when_nothing_exists(self) -> None:
        git = GitFixture(missing=("refs/heads/", "refs/remotes/"))
        GitCli(run=git).add_worktree(Path("/repo"), Path("/repo/.wt/loop/a"), "loop/a", "develop")
        self.assertEqual(git.commands[0], ["check-ref-format", "--branch", "loop/a"])
        self.assertEqual(git.commands[-2], ["branch", "loop/a", "develop"])
        self.assertEqual(git.commands[-1], ["worktree", "add", "/repo/.wt/loop/a", "loop/a"])

    def test_new_branch_defaults_to_head(self) -> None:
        git = GitFixture(missing=("refs/",))
        GitCli(run=git).add_worktree(Path("/repo"), Path("/repo/.wt/a"), "a")
        self.assertEqual(git.commands[-2], ["branch", "a", "HEAD"])

    def test_remote_only_branch_starts_from_origin(self) -> None:
        git = GitFixture(missing=("refs/heads/",))
        GitCli(run=git).add_worktree(Path("/repo"), Path("/repo/.wt/a"), "a", "develop")
        self.assertEqual(git.commands[-2], ["branch", "a", "origin/a"])

    def test_existing_local_branch_is_reused_as_is(self) -> None:
        git = GitFixture()
        GitCli(run=git).add_worktree(Path("/repo"), Path("/repo/.wt/a"), "a")
        self.assertNotIn("branch", [command[0] for command in git.commands])
        self.assertEqual(git.commands[-1], ["worktree", "add", "/repo/.wt/a", "a"])


class GitRuntimeTests(unittest.TestCase):
    def runtime(self, git: GitFixture) -> GitRuntime:
        return GitRuntime(GitCli(run=git))

    def test_head_makes_no_git_calls_and_yields_repository(self) -> None:
        git = GitFixture()
        with self.runtime(git).open(Path("/repo"), GitOptions(Path("wt"))) as target:
            self.assertEqual(target, Path("/repo"))
        self.assertEqual(git.commands, [])

    def test_head_yields_repository_path(self) -> None:
        git = GitFixture()
        options = GitOptions(repository_path=Path("services/api"), strategy=HeadStrategy())
        with self.runtime(git).open(Path("/repo"), options) as target:
            self.assertEqual(target, Path("/repo/services/api"))

    def test_head_ignores_root_outside_cwd(self) -> None:
        with self.runtime(GitFixture()).open(Path("/repo"), GitOptions(Path("../x"))):
            pass

    def test_merge_to_head_merges_then_deletes_temp_branch(self) -> None:
        git = GitFixture(missing=("refs/",))
        options = GitOptions(Path("wt"), strategy=MergeToHeadStrategy())
        with self.runtime(git).open(Path("/repo"), options) as target:
            self.assertRegex(target.name, r"^tmp_[0-9a-f]{8}$")
            self.assertEqual(target.parent, Path("/repo/wt"))
        branch = target.name
        self.assertEqual(
            [command[0] for command in git.commands],
            ["check-ref-format", "show-ref", "show-ref", "branch", "worktree", "add", "diff", "worktree", "merge", "branch"],
        )
        self.assertEqual(git.commands[3], ["branch", branch, "HEAD"])
        self.assertEqual(git.commands[-3], ["worktree", "remove", str(target)])
        self.assertEqual(git.commands[-2], ["merge", "--ff-only", branch])
        self.assertEqual(git.commands[-1], ["branch", "-d", branch])

    def test_merge_to_head_keeps_temp_branch_unmerged_on_failure(self) -> None:
        git = GitFixture(missing=("refs/",))
        options = GitOptions(Path("wt"), strategy=MergeToHeadStrategy())
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with self.runtime(git).open(Path("/repo"), options):
                raise RuntimeError("boom")
        self.assertEqual(git.commands[-1][:2], ["worktree", "remove"])
        self.assertNotIn("merge", [command[0] for command in git.commands])

    def test_branch_fetches_and_uses_base_branch_for_new_branch(self) -> None:
        git = GitFixture(missing=("refs/",))
        options = GitOptions(Path("wt"), strategy=BranchStrategy("loop/a", "develop"))
        with self.runtime(git).open(Path("/repo"), options) as target:
            self.assertEqual(target, Path("/repo/wt/loop/a"))
        self.assertEqual(git.commands[0], ["fetch", "--all", "--prune"])
        self.assertIn(["branch", "loop/a", "develop"], git.commands)
        self.assertEqual(git.commands[-1], ["worktree", "remove", "/repo/wt/loop/a"])
        self.assertNotIn("merge", [command[0] for command in git.commands])

    def test_branch_base_defaults_to_head(self) -> None:
        git = GitFixture(missing=("refs/",))
        options = GitOptions(Path("wt"), strategy=BranchStrategy("loop/a"))
        with self.runtime(git).open(Path("/repo"), options):
            pass
        self.assertIn(["branch", "loop/a", "HEAD"], git.commands)

    def test_worktree_is_removed_on_error(self) -> None:
        git = GitFixture(missing=("refs/",))
        options = GitOptions(Path("wt"), strategy=BranchStrategy("loop/a"))
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with self.runtime(git).open(Path("/repo"), options):
                raise RuntimeError("boom")
        self.assertEqual(git.commands[-1], ["worktree", "remove", "/repo/wt/loop/a"])

    def test_repository_path_selects_git_c_target(self) -> None:
        git = GitFixture(missing=("refs/",))
        options = GitOptions(Path("wt"), Path("services/api"), BranchStrategy("loop/a"))
        with self.runtime(git).open(Path("/repo"), options):
            pass
        self.assertEqual(git.repositories, {"/repo/services/api", "/repo/wt/loop/a"})

    def test_root_outside_cwd_is_rejected_for_worktree_strategies(self) -> None:
        for strategy in (MergeToHeadStrategy(), BranchStrategy("a")):
            with self.subTest(strategy=strategy):
                with self.assertRaisesRegex(ValueError, "must be inside"):
                    with self.runtime(GitFixture()).open(Path("/repo"), GitOptions(Path("../x"), strategy=strategy)):
                        pass


class DryRunTests(unittest.TestCase):
    def dry_run(self, strategy: object) -> str:
        agent = Agent(AgentOptions(dry_run=True)).with_git(GitOptions(Path("wt"), strategy=strategy)).with_docker()
        output = StringIO()
        with redirect_stdout(output):
            agent.create().run(AgentRequest("hello"))
        self.assertTrue(all(line.startswith("[dry-run] ") for line in output.getvalue().splitlines()))
        return output.getvalue()

    def test_branch_logs_every_step_without_running_git(self) -> None:
        text = self.dry_run(BranchStrategy("loop/a"))
        for expected in ("fetch --all --prune", "worktree add", "docker image=", "copilot -p hello", "worktree remove"):
            self.assertIn(expected, text)

    def test_merge_to_head_logs_merge_and_branch_delete(self) -> None:
        text = self.dry_run(MergeToHeadStrategy())
        for expected in ("merge --ff-only tmp_", "branch -d tmp_"):
            self.assertIn(expected, text)

    def test_open_logs_both_clis_between_one_worktree_pair(self) -> None:
        builder = Agent(AgentOptions(dry_run=True)).with_git(GitOptions(Path("wt"), strategy=BranchStrategy("loop/a")))
        output = StringIO()
        with redirect_stdout(output), builder.open() as wt:
            wt.agent(PLANNER).run(AgentRequest("plan"))
            wt.agent(DEVELOPER).run(AgentRequest("implement"))
        text = output.getvalue()
        self.assertEqual(text.count("worktree add"), 1)
        self.assertEqual(text.count("worktree remove"), 1)
        self.assertLess(text.index("copilot -p plan"), text.index("codex exec --json"))
        self.assertIn("--model gpt-5-codex", text)

    def test_head_logs_no_git_commands(self) -> None:
        self.assertNotIn("git -C", self.dry_run(HeadStrategy()))


if __name__ == "__main__":
    unittest.main()

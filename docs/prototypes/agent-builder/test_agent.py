"""Run with: python -m unittest discover -s docs/prototypes/agent-builder -p 'test_*.py'"""

from __future__ import annotations

import json
import subprocess
import tempfile
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
    AgentCliHook,
    AgentCliHookPoint,
    AgentCliHookWiring,
    PromptSubmittedAgentCliHook,
    PreToolAgentCliHook,
    SessionEndAgentCliHook,
    SessionStartAgentCliHook,
    RunFinishedLoopHook,
    WorktreeReadyLoopHook,
    WorktreeRemovingLoopHook,
    LoopHookPoint,
    UnsupportedAgentCliHookPoint,
    AgentContext,
    AgentOptions,
    AgentProfile,
    AgentRequest,
    AgentResult,
    BranchStrategy,
    CliAgentClient,
    CliOutcome,
    CodexCli,
    CopilotCli,
    DockerAgent,
    DockerRuntime,
    GitAgent,
    GitCli,
    GitOptions,
    GitRuntime,
    HeadStrategy,
    LoopHook,
    LoopHookError,
    MemorySessionStore,
    MergeToHeadStrategy,
    NativeHandle,
    ProcessCliRunner,
    Resume,
    RunContext,
    SessionCliMismatch,
    SessionHandleMissing,
    SessionName,
    Start,
    Turn,
    codex,
    copilot,
)
from agent.clis.codex_cli import _parse_codex_events


def repo_context() -> RunContext:
    return RunContext(repo_agent_context())


def repo_agent_context() -> AgentContext:
    return AgentContext(cwd=Path("/repo"))


class RecordingRunner:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.contexts: list[RunContext] = []
        self.requests: list[AgentRequest] = []
        self.profiles: list[AgentProfile] = []
        self.turns: list[Turn] = []

    def run(self, profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext) -> CliOutcome:
        self.events.append("cli.run")
        self.profiles.append(profile)
        self.contexts.append(context)
        self.requests.append(request)
        self.turns.append(turn)
        if self.fail:
            raise RuntimeError("runner failed")
        handle = turn.handle if isinstance(turn, Resume) else NativeHandle(profile.cli.name, f"{profile.cli.name}{len(self.requests)}")
        return CliOutcome(f"done:{request.prompt}", handle, 0)


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

    def configure(self, context: AgentContext) -> AgentContext:
        from dataclasses import replace

        self.events.append("docker.configure")
        return replace(context, docker_image="test-image")


class ClosingClient:
    def __init__(self) -> None:
        self.close_count = 0

    def run(self, request: AgentRequest, context: AgentContext | None = None) -> AgentResult:
        return AgentResult(request.prompt, SessionName("s"), 0)

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

    def test_run_returns_result_with_session_name(self) -> None:
        result = self.builder().create().run(AgentRequest("hello"), repo_agent_context())
        self.assertEqual(result.output, "done:hello")
        self.assertTrue(result.session.value.startswith("loop-"))

    def test_runs_are_stateless_without_session(self) -> None:
        client = self.builder().create()
        client.run(AgentRequest("one"), repo_agent_context())
        client.run(AgentRequest("two"), repo_agent_context())
        first, second = self.runner.turns
        self.assertIsInstance(first, Start)
        self.assertIsInstance(second, Start)
        self.assertNotEqual(first.name, second.name)
        self.assertTrue(first.name.value.startswith("loop-"))

    def test_session_is_shared_between_runs_in_one_cwd(self) -> None:
        client = self.builder().with_session().create()
        first = client.run(AgentRequest("one"), repo_agent_context())
        second = client.run(AgentRequest("two"), repo_agent_context())
        self.assertEqual(second.session, first.session)
        self.assertIsInstance(self.runner.turns[0], Start)
        self.assertEqual(self.runner.turns[1], Resume(first.session, NativeHandle("copilot", "copilot1")))

    def test_named_session(self) -> None:
        result = self.builder().with_session().create(session=SessionName("feat")).run(AgentRequest("x"), repo_agent_context())
        self.assertEqual(result.session, SessionName("feat"))

    def test_session_without_with_session_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.builder().create(session=SessionName("a"))
        with self.assertRaises(ValueError):
            with self.builder().open(session=SessionName("a")):
                pass
        with self.builder().open() as wt:
            with self.assertRaises(ValueError):
                wt.agent(session=SessionName("a"))

    def test_copilot_saves_handle_before_run(self) -> None:
        store = MemorySessionStore()
        self.runner.fail = True
        name = SessionName("n")
        client = AgentBuilder(self.runner, self.git, self.docker, sessions=store).with_session().create(session=name)
        with self.assertRaises(RuntimeError):
            client.run(AgentRequest("x"), repo_agent_context())
        self.assertEqual(store.get(name, "copilot"), NativeHandle("copilot", "n"))
        self.runner.fail = False
        client.run(AgentRequest("x"), repo_agent_context())
        self.assertIsInstance(self.runner.turns[-1], Resume)

    def test_cli_mismatch_raises(self) -> None:
        store = MemorySessionStore()
        name = SessionName("n")
        store._sessions[(name, "copilot")] = NativeHandle("codex", "t")
        client = AgentBuilder(self.runner, self.git, self.docker, sessions=store).with_session().create(session=name)
        with self.assertRaises(SessionCliMismatch):
            client.run(AgentRequest("x"), repo_agent_context())

    def test_session_sees_the_git_worktree(self) -> None:
        client = self.builder().with_git().with_session().create()
        self.assertIsInstance(client, GitAgent)
        self.assertIsInstance(client._inner, CliAgentClient)
        client.run(AgentRequest("one"), repo_agent_context())
        client.run(AgentRequest("two"), repo_agent_context())
        self.assertIsInstance(self.runner.turns[1], Resume)

    def test_session_name_validation(self) -> None:
        for bad in ("", "a b"):
            with self.assertRaises(ValueError):
                SessionName(bad)
        self.assertTrue(SessionName.new().value.startswith("loop-"))

    def test_one_chain_creates_git_outer_and_docker_inner(self) -> None:
        builder = self.builder()
        self.assertIs(builder.with_git(), builder)
        self.assertIs(builder.with_docker(), builder)
        client = builder.create()
        self.assertIsInstance(client, GitAgent)
        self.assertIsInstance(client._inner, DockerAgent)
        self.assertIsInstance(client._inner._inner, CliAgentClient)
        self.assertEqual(client.run(AgentRequest("hello"), repo_agent_context()).output, "done:hello")
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
        self.builder().with_docker().create().run(AgentRequest("hello"), repo_agent_context())
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
        name = SessionName("n")
        with self.builder.with_session().open(session=name) as wt:
            wt.agent(DEVELOPER).run(AgentRequest("one"))
            wt.agent(PLANNER).run(AgentRequest("two"))
            wt.agent(DEVELOPER).run(AgentRequest("three"))
            wt.agent(PLANNER).run(AgentRequest("four"))
        one, two, three, four = self.runner.turns
        self.assertEqual(one, Start(name))
        self.assertEqual(two, Start(name))
        self.assertEqual(three, Resume(name, NativeHandle("codex", "codex1")))
        self.assertEqual(four, Resume(name, NativeHandle("copilot", "copilot2")))

    def test_agent_session_override_starts_fresh(self) -> None:
        with self.builder.with_session().open(session=SessionName("n")) as wt:
            wt.agent(PLANNER).run(AgentRequest("one"))
            wt.agent(PLANNER, session=SessionName.new()).run(AgentRequest("review"))
            wt.agent(PLANNER).run(AgentRequest("two"))
        self.assertIsInstance(self.runner.turns[1], Start)
        self.assertNotEqual(self.runner.turns[1].name, SessionName("n"))
        self.assertIsInstance(self.runner.turns[2], Resume)

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
    hook_points: frozenset[AgentCliHookPoint] = frozenset()

    def hook_wiring(self, hooks: tuple[AgentCliHook, ...], turn: Turn, workdir: Path) -> AgentCliHookWiring:
        return AgentCliHookWiring()

    def native_point(self, point: AgentCliHookPoint) -> str:
        return point.value

    def handle_for_new(self, name: SessionName) -> NativeHandle | None:
        return NativeHandle("user", "fixed")

    def command(self, request: AgentRequest, profile: AgentProfile, turn: Turn, context: RunContext) -> list[str]:
        return ["user-cli", request.prompt, turn.name.value, *profile.args]

    def parse(self, stdout: str, turn: Turn, exit_code: int) -> CliOutcome:
        return CliOutcome(stdout.upper(), NativeHandle("user", "fixed"), exit_code)


class CliTests(unittest.TestCase):
    def test_user_defined_cli_runs_through_process_runner(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake(argv: list[str], **kwargs: object) -> object:
            calls.append((argv, kwargs))
            return type("P", (), {"stdout": "out", "returncode": 0})()

        cli: AgentCli = UserCli()
        turn = Start(SessionName("s"))
        result = ProcessCliRunner(run=fake).run(AgentProfile(cli, args=("-x",)), AgentRequest("hi"), turn, repo_context())
        self.assertEqual(result, CliOutcome("OUT", NativeHandle("user", "fixed"), 0))
        self.assertEqual(calls[0][0], ["user-cli", "hi", "s", "-x"])
        self.assertEqual(calls[0][1]["cwd"], Path("/repo"))

    def test_copilot_command(self) -> None:
        profile = replace(PLANNER, args=("--x",))
        tail = ["--allow-all-tools", "--model", "claude-opus-4.5", "--reasoning-effort", "high", "--x"]
        start = CopilotCli().command(AgentRequest("p"), profile, Start(SessionName("n")), repo_context())
        self.assertEqual(start, ["copilot", "-p", "p", "--name", "n", *tail])
        resume = CopilotCli().command(
            AgentRequest("p"), profile, Resume(SessionName("n"), NativeHandle("copilot", "h")), repo_context()
        )
        self.assertEqual(resume, ["copilot", "-p", "p", "--resume=h", *tail])

    def test_codex_command_new_and_resume(self) -> None:
        profile = replace(DEVELOPER, args=("--x",))
        name = SessionName("n")
        new = CodexCli().command(AgentRequest("p"), profile, Start(name), repo_context())
        self.assertEqual(
            new,
            ["codex", "exec", "--json", "--sandbox", "workspace-write", "--model", "gpt-5-codex",
             "-c", "model_reasoning_effort=high", "--x", "p"],
        )
        resumed = CodexCli().command(AgentRequest("p"), profile, Resume(name, NativeHandle("codex", "t1")), repo_context())
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
        outcome = CodexCli().parse(stdout, Start(SessionName("n")), 0)
        self.assertEqual(outcome, CliOutcome("last", NativeHandle("codex", "t1"), 0))

    def test_codex_parse_without_thread_id(self) -> None:
        with self.assertRaises(SessionHandleMissing):
            CodexCli().parse("", Start(SessionName("n")), 0)
        handle = NativeHandle("codex", "t1")
        self.assertEqual(CodexCli().parse("", Resume(SessionName("n"), handle), 0).handle, handle)


class FakeProcess:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class GitFixture:
    """Records git invocations (list commands) and hook calls (shell strings); `missing` ref fragments make show-ref fail."""

    def __init__(
        self,
        missing: tuple[str, ...] = (),
        failing: tuple[str, ...] = (),
        timing_out: tuple[str, ...] = (),
        remove_fails: bool = False,
    ) -> None:
        self.missing = missing
        self.failing = failing
        self.timing_out = timing_out
        self.remove_fails = remove_fails
        self.commands: list[list[str]] = []
        self.hooks: list[tuple[str, dict]] = []
        self.repositories: set[str] = set()

    def __call__(self, command: list[str] | str, **kwargs: object) -> FakeProcess:
        if isinstance(command, str):
            self.hooks.append((command, kwargs))
            self.commands.append(["hook", command])
            if command in self.timing_out:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            if command in self.failing:
                return FakeProcess(1, "out-", "err")
            return FakeProcess(0)
        self.repositories.add(command[2])
        self.commands.append(command[3:])
        if self.remove_fails and command[3:5] == ["worktree", "remove"]:
            raise subprocess.CalledProcessError(1, command)
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


class LoopHookTests(unittest.TestCase):
    def options(self, *commands: str | LoopHook, strategy: object | None = None) -> GitOptions:
        return GitOptions(
            Path("wt"),
            strategy=strategy or BranchStrategy("loop/a"),
            loop_hooks=tuple(
                WorktreeReadyLoopHook(command, timeout_sec=5) if isinstance(command, str) else command
                for command in commands
            ),
        )

    def open(self, git: GitFixture, options: GitOptions):
        return GitRuntime(GitCli(run=git)).open(Path("/repo"), options)

    def test_hook_validation(self) -> None:
        with self.assertRaises(ValueError):
            WorktreeReadyLoopHook("")
        with self.assertRaises(ValueError):
            WorktreeReadyLoopHook("x", timeout_sec=0)

    def test_head_rejects_hooks_but_worktree_strategies_accept(self) -> None:
        with self.assertRaisesRegex(ValueError, "need a worktree strategy"):
            GitOptions(loop_hooks=(WorktreeReadyLoopHook("x"),))
        self.options("x")
        self.options("x", strategy=MergeToHeadStrategy())

    def test_hooks_run_in_order_after_add_with_cwd_timeout_and_env(self) -> None:
        git = GitFixture(missing=("refs/",))
        body: list[int] = []
        with self.open(git, self.options("one", "two")):
            body.append(len(git.hooks))
        names = [command[:2] for command in git.commands]
        self.assertEqual(body, [2])
        self.assertEqual([hook for hook, _ in git.hooks], ["one", "two"])
        self.assertLess(names.index(["worktree", "add"]), [n[0] for n in names].index("hook"))
        for _, kwargs in git.hooks:
            self.assertEqual(kwargs["cwd"], Path("/repo/wt/loop/a"))
            self.assertEqual(kwargs["timeout"], 5)
            self.assertEqual(kwargs["env"]["LOOP_REPOSITORY"], "/repo")
            self.assertEqual(kwargs["env"]["LOOP_WORKTREE"], "/repo/wt/loop/a")
            self.assertEqual(kwargs["env"]["LOOP_HOOK_POINT"], "worktree-ready")

    def test_failing_hook_force_removes_and_skips_body(self) -> None:
        git = GitFixture(missing=("refs/",), failing=("one",))
        entered = False
        with self.assertRaises(LoopHookError) as caught:
            with self.open(git, self.options("one", "two")):
                entered = True
        self.assertFalse(entered)
        self.assertEqual(caught.exception.command, "one")
        self.assertEqual(caught.exception.point, LoopHookPoint.WORKTREE_READY)
        self.assertIn("worktree-ready hook failed", str(caught.exception))
        self.assertEqual(caught.exception.output, "out-err")
        self.assertEqual([hook for hook, _ in git.hooks], ["one"])
        self.assertEqual(git.commands[-1], ["worktree", "remove", "--force", "/repo/wt/loop/a"])
        names = [command[0] for command in git.commands]
        for forbidden in ("diff", "commit", "merge"):
            self.assertNotIn(forbidden, names)

    def test_timed_out_hook_raises(self) -> None:
        git = GitFixture(missing=("refs/",), timing_out=("slow",))
        with self.assertRaisesRegex(LoopHookError, "timed out after"):
            with self.open(git, self.options("slow")):
                pass

    def test_failing_hook_under_merge_to_head_does_not_merge(self) -> None:
        git = GitFixture(missing=("refs/",), failing=("one",))
        with self.assertRaises(LoopHookError):
            with self.open(git, self.options("one", strategy=MergeToHeadStrategy())):
                pass
        self.assertNotIn("merge", [command[0] for command in git.commands])
        self.assertNotIn(["branch", "-d"], [command[:2] for command in git.commands])

    def test_failed_forced_removal_is_noted_on_hook_error(self) -> None:
        git = GitFixture(missing=("refs/",), failing=("one",), remove_fails=True)
        with self.assertRaises(LoopHookError) as caught:
            with self.open(git, self.options("one")):
                pass
        self.assertTrue(any("removal" in note for note in caught.exception.__notes__))

    def builder(self, git: GitFixture, events: list[str]) -> tuple[AgentBuilder, RecordingRunner]:
        runner = RecordingRunner(events)
        builder = AgentBuilder(
            runner, GitRuntime(GitCli(run=git)), DockerRuntime(), AgentContext(cwd=Path("/repo"))
        )
        return builder, runner

    def test_builder_failing_hook_never_starts_agent(self) -> None:
        git = GitFixture(missing=("refs/",), failing=("one",))
        builder, runner = self.builder(git, [])
        builder = builder.with_git(self.options("one"))
        with self.assertRaises(LoopHookError):
            builder.create().run(AgentRequest("x"))
        self.assertEqual(runner.contexts, [])
        with self.assertRaises(LoopHookError):
            with builder.open():
                pass

    def test_hooks_fire_per_run_for_create_and_once_for_open(self) -> None:
        git = GitFixture(missing=("refs/",))
        builder, _ = self.builder(git, [])
        builder = builder.with_git(self.options("one", "two"))
        client = builder.create()
        client.run(AgentRequest("a"))
        client.run(AgentRequest("b"))
        self.assertEqual(len(git.hooks), 4)
        git.hooks.clear()
        with builder.open() as wt:
            wt.agent().run(AgentRequest("a"))
            wt.agent().run(AgentRequest("b"))
        self.assertEqual([hook for hook, _ in git.hooks], ["one", "two"])


class HookPointClassTests(unittest.TestCase):
    def check_one_to_one(self, base: type, points: type, suffix: str) -> None:
        subclasses = base.__subclasses__()
        self.assertEqual(sorted(cls.point for cls in subclasses), sorted(points))
        for cls in subclasses:
            words = cls.point.name.title().replace("_", "")
            self.assertEqual(cls.__name__, f"{words}{suffix}")

    def test_agent_cli_hook_classes_match_points(self) -> None:
        self.check_one_to_one(AgentCliHook, AgentCliHookPoint, "AgentCliHook")

    def test_loop_hook_classes_match_points(self) -> None:
        self.check_one_to_one(LoopHook, LoopHookPoint, "LoopHook")

    def test_bases_are_not_instantiable(self) -> None:
        for base in (AgentCliHook, LoopHook):
            with self.assertRaises(TypeError):
                base("x")

    def test_hooks_at_keeps_declared_order(self) -> None:
        a, b, c = WorktreeReadyLoopHook("a"), RunFinishedLoopHook("b"), WorktreeReadyLoopHook("c")
        options = GitOptions(strategy=MergeToHeadStrategy(), loop_hooks=(a, b, c))
        self.assertEqual(options.hooks_at(LoopHookPoint.WORKTREE_READY), (a, c))
        self.assertEqual(options.hooks_at(LoopHookPoint.WORKTREE_REMOVING), ())


class LoopHookLifecycleTests(unittest.TestCase):
    def options(self, *hooks: LoopHook) -> GitOptions:
        return GitOptions(Path("wt"), strategy=MergeToHeadStrategy(), loop_hooks=hooks)

    def open(self, git: GitFixture, options: GitOptions):
        return GitRuntime(GitCli(run=git)).open(Path("/repo"), options)

    def names(self, git: GitFixture) -> list[str]:
        return [" ".join(command[:2]) for command in git.commands]

    def test_removing_runs_after_commit_before_remove(self) -> None:
        git = GitFixture(missing=("refs/",))
        with self.open(git, self.options(WorktreeRemovingLoopHook("bye"))) as target:
            pass
        names = self.names(git)
        self.assertLess(names.index("diff --cached"), names.index("hook bye"))
        self.assertLess(names.index("hook bye"), names.index("worktree remove"))
        _, kwargs = git.hooks[0]
        self.assertEqual(kwargs["cwd"], target)
        self.assertEqual(kwargs["env"]["LOOP_HOOK_POINT"], "worktree-removing")

    def test_removing_hook_failure_still_removes_worktree(self) -> None:
        git = GitFixture(missing=("refs/",), failing=("bye",))
        with self.assertRaises(LoopHookError) as caught:
            with self.open(git, self.options(WorktreeRemovingLoopHook("bye"))):
                pass
        self.assertEqual(caught.exception.point, LoopHookPoint.WORKTREE_REMOVING)
        self.assertIn("worktree remove", self.names(git))
        self.assertNotIn("merge --ff-only", self.names(git))

    def test_removing_runs_when_body_fails(self) -> None:
        git = GitFixture(missing=("refs/",))
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with self.open(git, self.options(WorktreeRemovingLoopHook("bye"))):
                raise RuntimeError("boom")
        self.assertEqual(git.hooks, [])

    def test_run_finished_runs_after_merge_in_repository(self) -> None:
        git = GitFixture(missing=("refs/",))
        with self.open(git, self.options(RunFinishedLoopHook("done"))) as target:
            pass
        names = self.names(git)
        self.assertGreater(names.index("hook done"), names.index("branch -d"))
        _, kwargs = git.hooks[0]
        self.assertEqual(kwargs["cwd"], Path("/repo"))
        self.assertEqual(kwargs["env"]["LOOP_WORKTREE"], str(target))
        self.assertEqual(kwargs["env"]["LOOP_HOOK_POINT"], "run-finished")

    def test_run_finished_skipped_when_body_fails(self) -> None:
        git = GitFixture(missing=("refs/",))
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with self.open(git, self.options(RunFinishedLoopHook("done"))):
                raise RuntimeError("boom")
        self.assertEqual(git.hooks, [])


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

    def test_open_with_session_logs_name_and_resume(self) -> None:
        builder = Agent(AgentOptions(dry_run=True)).with_session()
        output = StringIO()
        with redirect_stdout(output), builder.open(session=SessionName("ticket-123")) as wt:
            wt.agent(PLANNER).run(AgentRequest("plan"))
            wt.agent(DEVELOPER).run(AgentRequest("a"))
            wt.agent(DEVELOPER).run(AgentRequest("b"))
        text = output.getvalue()
        self.assertIn("--name ticket-123", text)
        self.assertIn("codex exec resume dry-", text)

    def test_head_logs_no_git_commands(self) -> None:
        self.assertNotIn("git -C", self.dry_run(HeadStrategy()))


class NoPointsCli(UserCli):
    name = "nopoints"
    hook_points = frozenset({AgentCliHookPoint.SESSION_START})


class AgentCliHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[str] = []
        self.runner = RecordingRunner(self.events)
        self.builder = AgentBuilder(self.runner, RecordingGit(self.events), RecordingDocker(self.events), AgentContext(cwd=Path("/repo")))

    def test_no_hooks_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.builder.with_agent_cli_hooks()

    def test_str_becomes_session_lifecycle_hooks(self) -> None:
        self.builder.with_agent_cli_hooks("cmd").create().run(AgentRequest("x"))
        self.assertEqual(
            self.runner.contexts[0].agent_cli_hooks, (SessionStartAgentCliHook("cmd"), SessionEndAgentCliHook("cmd"))
        )

    def test_invalid_hook_raises(self) -> None:
        for args in (("",), ("x", 0)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                SessionStartAgentCliHook(*args)

    def test_hooks_reach_runner_in_order_with_git_and_open(self) -> None:
        first, second = SessionStartAgentCliHook("a"), SessionEndAgentCliHook("b")
        builder = self.builder.with_git().with_agent_cli_hooks(first, second)
        builder.create().run(AgentRequest("x"))
        with builder.open() as wt:
            wt.agent().run(AgentRequest("y"))
        for context in self.runner.contexts:
            self.assertEqual(context.agent_cli_hooks, (first, second))

    def test_copilot_accepts_prompt_submitted(self) -> None:
        hook = PromptSubmittedAgentCliHook("c")
        self.builder.with_agent_cli_hooks(hook).create(AgentProfile(copilot)).run(AgentRequest("x"))

    def test_unsupported_point_fails_before_git(self) -> None:
        builder = self.builder.with_git().with_agent_cli_hooks(PreToolAgentCliHook("c"))
        with self.assertRaises(UnsupportedAgentCliHookPoint):
            builder.create(AgentProfile(NoPointsCli()))
        self.assertEqual(self.events, [])

    def test_codex_needs_trust_bypass(self) -> None:
        builder = self.builder.with_agent_cli_hooks("c")
        with self.assertRaises(UnsupportedAgentCliHookPoint):
            builder.create(AgentProfile(codex))
        builder.create(AgentProfile(CodexCli(hook_trust_bypass=True)))

    def test_copilot_wiring(self) -> None:
        hooks = (
            PromptSubmittedAgentCliHook("my cmd", 7),
            SessionEndAgentCliHook("my cmd", 7),
        )
        wiring = CopilotCli().hook_wiring(hooks, Start(SessionName("s1")), Path("/w"))
        (path, content), = wiring.files.items()
        self.assertEqual(path.parent, Path(".github/hooks"))
        self.assertRegex(path.name, r"^loop-[0-9a-f]+\.json$")
        self.assertEqual(wiring.env, {"GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS": "true"})
        data = json.loads(content)
        self.assertEqual(data["version"], 1)
        self.assertEqual(set(data["hooks"]), {"userPromptSubmitted", "sessionEnd"})
        entry = data["hooks"]["sessionEnd"][0]
        self.assertEqual(entry["timeoutSec"], 7)
        for part in ("shim.py", "--session s1", "'my cmd'"):
            self.assertIn(part, entry["bash"])

    def test_codex_command_with_hooks(self) -> None:
        cli = CodexCli(hook_trust_bypass=True)
        hooks = (SessionStartAgentCliHook("start"), SessionEndAgentCliHook("end"))
        context = RunContext(AgentContext(cwd=Path("/repo")), hooks)
        name = SessionName("n")
        argv = cli.command(AgentRequest("p"), AgentProfile(cli), Start(name), context)
        text = " ".join(argv)
        self.assertIn("hooks.SessionStart=", text)
        self.assertIn('matcher="startup|resume"', text)
        self.assertIn("timeout=3}", text)
        self.assertLess(argv.index("--dangerously-bypass-hook-trust"), argv.index("p"))
        resumed = cli.command(AgentRequest("p"), AgentProfile(cli), Resume(name, NativeHandle("codex", "t")), context)
        self.assertEqual(resumed[:4], ["codex", "exec", "resume", "t"])
        self.assertEqual(resumed[-1], "p")


class HookFilesTests(unittest.TestCase):
    def runner_with(self, cwd: Path, exclude: Path, seen: list[object], fail: bool = False) -> ProcessCliRunner:
        def fake(argv: list[str], **kwargs: object) -> object:
            if argv[:2] == ["git", "-C"]:
                return type("P", (), {"stdout": f"{exclude}\n", "returncode": 0})()
            seen.append(((cwd / ".github/hooks").exists() and list((cwd / ".github/hooks").glob("loop-*.json")), kwargs.get("env")))
            if fail:
                raise RuntimeError("boom")
            return type("P", (), {"stdout": "", "returncode": 0})()

        return ProcessCliRunner(run=fake)

    def test_file_written_during_run_removed_after_and_excluded_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd, exclude = Path(tmp), Path(tmp) / "info" / "exclude"
            seen: list[object] = []
            runner = self.runner_with(cwd, exclude, seen)
            context = RunContext(AgentContext(cwd=cwd), (SessionStartAgentCliHook("c"),))
            for _ in range(2):
                runner.run(AgentProfile(copilot), AgentRequest("x"), Start(SessionName("s")), context)
            for files, env in seen:
                self.assertTrue(files)
                self.assertEqual(env["GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS"], "true")
            self.assertEqual(list((cwd / ".github/hooks").glob("*")), [])
            self.assertEqual(exclude.read_text().splitlines().count(".github/hooks/loop-*.json"), 1)

    def test_file_removed_when_run_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp)
            runner = self.runner_with(cwd, cwd / "exclude", [], fail=True)
            context = RunContext(AgentContext(cwd=cwd), (SessionStartAgentCliHook("c"),))
            with self.assertRaises(RuntimeError):
                runner.run(AgentProfile(copilot), AgentRequest("x"), Start(SessionName("s")), context)
            self.assertEqual(list((cwd / ".github/hooks").glob("*")), [])

    def test_no_env_without_wiring_env(self) -> None:
        calls: list[dict[str, object]] = []

        def fake(argv: list[str], **kwargs: object) -> object:
            calls.append(kwargs)
            return type("P", (), {"stdout": "", "returncode": 0})()

        ProcessCliRunner(run=fake).run(AgentProfile(UserCli()), AgentRequest("x"), Start(SessionName("s")), repo_context())
        self.assertNotIn("env", calls[0])


class ShimTests(unittest.TestCase):
    def test_normalise_copilot_and_codex(self) -> None:
        from agent.hooks.shim import normalise

        copilot_payload = normalise("copilot", "session-start", {"sessionId": "s", "cwd": "/w", "source": "resume"})
        codex_payload = normalise("codex", "session-start", {"session_id": "s", "cwd": "/w", "source": "resume"})
        for payload in (copilot_payload, codex_payload):
            self.assertTrue(payload["resumed"])
            self.assertEqual(payload["handle"], "s")
        self.assertEqual(normalise("codex", "pre-tool", {"tool_name": "bash"})["tool"], "bash")

    def test_main_with_failing_command_exits_zero_silently(self) -> None:
        import sys
        from agent.hooks.shim import main

        out = StringIO()
        original = sys.stdin
        sys.stdin = StringIO("{}")
        try:
            with redirect_stdout(out):
                code = main(["--cli", "copilot", "--point", "session-end", "--session", "s", "--", "exit 3"])
        finally:
            sys.stdin = original
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "")


class HookDryRunTests(unittest.TestCase):
    def test_logs_hooks_without_writing_files(self) -> None:
        agent = Agent(AgentOptions(dry_run=True)).with_agent_cli_hooks(
            SessionStartAgentCliHook("start.sh"),
            SessionEndAgentCliHook("notify", 3),
        )
        output = StringIO()
        with redirect_stdout(output):
            agent.create(AgentProfile(copilot)).run(AgentRequest("x"))
            agent.create(AgentProfile(CodexCli(hook_trust_bypass=True))).run(AgentRequest("x"))
        text = output.getvalue()
        self.assertIn("hook copilot session-start -> sessionStart", text)
        self.assertIn("hook codex session-end -> SessionEnd", text)
        self.assertIn("timeout=3", text)


if __name__ == "__main__":
    unittest.main()

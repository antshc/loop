"""The fake agent CLI and worktree used to run a functional slice in isolation."""

from __future__ import annotations

from pathlib import Path

from loop import AgentRequest, AgentRunRecord, FakeCliRunner, FakeReply, FakeToolUse, Usage, WorktreeEvent, fake_agent


class Recorder:
    def __init__(self) -> None:
        self.runs: list[AgentRunRecord] = []
        self.worktrees: list[WorktreeEvent] = []

    def agent_run(self, record: AgentRunRecord) -> None:
        self.runs.append(record)

    def worktree(self, event: WorktreeEvent) -> None:
        self.worktrees.append(event)


def _runner() -> FakeCliRunner:
    reply = FakeReply("hello", Usage(input_tokens=10, output_tokens=5), (FakeToolUse("bash", seconds=2),), model="fake-1")
    return FakeCliRunner(lambda request, context: reply, speed=0)


def test_a_fake_agent_runs_in_a_temporary_directory_that_is_removed() -> None:
    with fake_agent(_runner()).with_git().open() as worktree:
        path = worktree.path
        result = worktree.agent().run(AgentRequest("hi"))
        assert path.is_dir() and result.output == "hello"
    assert not path.exists()


def test_an_observer_sees_the_fakes_usage_tools_and_worktree() -> None:
    runner, observer = _runner(), Recorder()

    with fake_agent(runner).with_git().with_observer(observer, readers=[runner]).open() as worktree:
        worktree.agent().run(AgentRequest("hi"))

    record = observer.runs[0]
    assert record.usage == Usage(input_tokens=10, output_tokens=5) and record.model == "fake-1"
    assert [tool.name for tool in record.tools] == ["bash"]
    assert [event.point for event in observer.worktrees] == ["ready", "removing", "removed"]


def test_without_the_fake_as_reader_usage_is_unobservable() -> None:
    observer = Recorder()

    with fake_agent(_runner()).with_observer(observer, readers=[]).open() as worktree:
        worktree.agent().run(AgentRequest("hi"))

    assert observer.runs[0].usage is None


def test_speed_scales_the_tool_time_the_fake_waits() -> None:
    slept: list[float] = []
    reply = FakeReply("ok", tools=(FakeToolUse("bash", seconds=4),))
    runner = FakeCliRunner(lambda request, context: reply, speed=0.5, sleep=slept.append)

    with fake_agent(runner).open() as worktree:
        worktree.agent().run(AgentRequest("hi"))

    assert slept == [2.0]

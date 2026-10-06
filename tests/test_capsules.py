from __future__ import annotations

import json
from pathlib import Path

import pytest

from orb import (
    AgentOptions,
    Capsule,
    CommandError,
    DEFAULT_COMPLETION_SIGNAL,
    DockerCapsule,
    InMemorySessionStore,
    NoCapsule,
    OrbError,
    copilot,
)
from orb.testing import FakeAgentClient, FakeCopilotCli, FakeDocker

CONTAINER_WORKSPACE = "/home/agent/workspace"


def _event(delta: str) -> str:
    return json.dumps({"type": "assistant.message_delta", "data": {"deltaContent": delta}})


def test_no_capsule_delegates_prompt_args_and_options_to_its_agent(tmp_path: Path) -> None:
    agent = FakeAgentClient(lambda prompt, options: f"{prompt}:{options.model}")

    with NoCapsule(tmp_path) as capsule:
        result = capsule.run(lambda executor: agent, "p=${{A}}", {"A": "1"}, AgentOptions(model="m"))

    assert isinstance(capsule, Capsule)
    assert capsule.workspace == str(tmp_path)
    assert capsule.isolated is False
    assert result.stdout == "p=1:m"


def test_no_capsule_executes_commands_on_the_host_in_the_workspace(tmp_path: Path) -> None:
    capsule = NoCapsule(tmp_path)

    assert capsule.exec("pwd").strip() == str(tmp_path)
    assert capsule.executor("echo hi && false").returncode == 1
    with pytest.raises(CommandError):
        capsule.exec("exit 3")


def test_no_capsule_runs_copilot_through_an_injected_executor(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: "done")

    result = NoCapsule(tmp_path, executor=cli).run(copilot(InMemorySessionStore()), "hi")

    assert result.stdout == "done" and cli.calls[0][2] == "hi"


def test_no_capsule_runs_a_different_agent_on_each_run(tmp_path: Path) -> None:
    planner = FakeAgentClient(lambda prompt, options: "planned")
    reviewer = FakeAgentClient(lambda prompt, options: "reviewed")
    capsule = NoCapsule(tmp_path)

    first = capsule.run(lambda executor: planner, "plan")
    second = capsule.run(lambda executor: reviewer, "review")

    assert (first.stdout, second.stdout) == ("planned", "reviewed")
    assert planner.calls[0][0] == "plan" and reviewer.calls[0][0] == "review"


def test_docker_capsule_starts_a_container_and_runs_the_agent_inside_it(tmp_path: Path) -> None:
    cli = FakeCopilotCli(shell=lambda command: "listing\n")
    docker = FakeDocker(cli)
    capsule = DockerCapsule(
        tmp_path,
        image_name="orb:test",
        container_uid=1000,
        env={"A": "1"},
        cpus=2,
        docker=docker,
    )

    result = capsule.run(copilot(InMemorySessionStore()), "see !`ls`", options=AgentOptions(session_key="k"))
    capsule.close()

    start = next(call for call in docker.calls if call[1:3] == ("run", "-d"))
    assert f"{tmp_path}:{CONTAINER_WORKSPACE}:z" in start and "A=1" in start and "2" in start
    assert start[-1] == "orb:test"
    container = start[start.index("--name") + 1]
    exec_prefix = ("docker", "exec", "-w", CONTAINER_WORKSPACE, container)
    assert ("sh", "-c", "ls") == next(c for c in docker.calls if c[:5] == exec_prefix and "ls" in c)[5:]
    assert any(c[:5] == exec_prefix and c[5] == "copilot" for c in docker.calls)
    assert cli.calls[0][2] == "see listing" and result.success
    assert capsule.workspace == CONTAINER_WORKSPACE
    assert capsule.isolated is True
    assert ("docker", "stop", container) in docker.calls and ("docker", "rm", container) in docker.calls


def test_docker_capsule_mounts_the_worktrees_git_dir(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: /host/repo/.git/worktrees/w\n")
    docker = FakeDocker(FakeCopilotCli())

    capsule = DockerCapsule(tmp_path, container_uid=1000, docker=docker)
    capsule.close()

    start = next(call for call in docker.calls if call[1:3] == ("run", "-d"))
    assert "/host/repo/.git:/host/repo/.git:z" in start
    assert start[-1] == "orb:repo"


def test_docker_capsule_runs_a_different_agent_on_each_run(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(shell=lambda command: ""))
    capsule = DockerCapsule(tmp_path, container_uid=1000, docker=docker)
    planner = FakeAgentClient(lambda prompt, options: "planned")
    reviewer = FakeAgentClient(lambda prompt, options: "reviewed")

    first = capsule.run(lambda executor: planner, "plan")
    second = capsule.run(lambda executor: reviewer, "review")
    capsule.close()

    assert (first.stdout, second.stdout) == ("planned", "reviewed")
    assert planner.calls[0][0] == "plan" and reviewer.calls[0][0] == "review"


def test_no_capsule_streams_copilot_output_and_stops_at_the_completion_signal(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event(DEFAULT_COMPLETION_SIGNAL), _event("never")])

    result = NoCapsule(tmp_path, executor=cli).run(copilot(InMemorySessionStore()), "go")

    assert result.completed and result.success
    assert "never" not in result.stdout
    assert cli.terminated


def test_docker_capsule_streams_copilot_output_and_stops_at_the_completion_signal(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event(DEFAULT_COMPLETION_SIGNAL), _event("never")])
    docker = FakeDocker(cli)
    capsule = DockerCapsule(tmp_path, image_name="orb:test", container_uid=1000, docker=docker)

    result = capsule.run(copilot(InMemorySessionStore()), "go")
    capsule.close()

    assert result.completed and result.success
    assert "never" not in result.stdout
    assert cli.terminated


def test_docker_capsule_rejects_an_image_built_for_another_uid(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), image_user="999")

    with pytest.raises(OrbError, match="UID mismatch"):
        DockerCapsule(tmp_path, image_name="orb:x", container_uid=1000, docker=docker)


def test_no_capsule_closing_twice_is_a_no_op(tmp_path: Path) -> None:
    capsule = NoCapsule(tmp_path)

    capsule.close()
    capsule.close()


def test_docker_capsule_closing_twice_stops_and_removes_the_container_once(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli())
    capsule = DockerCapsule(tmp_path, container_uid=1000, docker=docker)

    capsule.close()
    capsule.close()

    assert sum(1 for call in docker.calls if call[1] == "stop") == 1
    assert sum(1 for call in docker.calls if call[1] == "rm") == 1

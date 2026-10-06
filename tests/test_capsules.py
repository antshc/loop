from __future__ import annotations

import json
from pathlib import Path

import pytest

from orb import (
    AgentOptions,
    Capsule,
    CommandError,
    CommandExecutor,
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

    with NoCapsule(tmp_path, lambda executor: agent) as capsule:
        result = capsule.run("p=${{A}}", {"A": "1"}, AgentOptions(model="m"))

    assert isinstance(capsule, Capsule)
    assert capsule.workspace == str(tmp_path)
    assert result.stdout == "p=1:m"


def test_no_capsule_executes_commands_on_the_host_in_the_workspace(tmp_path: Path) -> None:
    captured: list[CommandExecutor] = []

    def factory(executor: CommandExecutor) -> FakeAgentClient:
        captured.append(executor)
        return FakeAgentClient()

    capsule = NoCapsule(tmp_path, factory)

    assert capsule.exec("pwd").strip() == str(tmp_path)
    assert captured[0]("echo hi && false").returncode == 1
    with pytest.raises(CommandError):
        capsule.exec("exit 3")


def test_no_capsule_runs_copilot_through_an_injected_executor(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: "done")

    result = NoCapsule(tmp_path, copilot(InMemorySessionStore()), executor=cli).run("hi")

    assert result.stdout == "done" and cli.calls[0][2] == "hi"


def test_docker_capsule_starts_a_container_and_runs_the_agent_inside_it(tmp_path: Path) -> None:
    cli = FakeCopilotCli(shell=lambda command: "listing\n")
    docker = FakeDocker(cli)
    capsule = DockerCapsule(
        tmp_path,
        copilot(InMemorySessionStore()),
        image_name="orb:test",
        container_uid=1000,
        env={"A": "1"},
        cpus=2,
        docker=docker,
    )

    result = capsule.run("see !`ls`", options=AgentOptions(session_key="k"))
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
    assert ("docker", "stop", container) in docker.calls and ("docker", "rm", container) in docker.calls


def test_docker_capsule_mounts_the_worktrees_git_dir(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: /host/repo/.git/worktrees/w\n")
    docker = FakeDocker(FakeCopilotCli())

    capsule = DockerCapsule(tmp_path, copilot(InMemorySessionStore()), container_uid=1000, docker=docker)
    capsule.close()

    start = next(call for call in docker.calls if call[1:3] == ("run", "-d"))
    assert "/host/repo/.git:/host/repo/.git:z" in start
    assert start[-1] == "orb:repo"


def test_no_capsule_streams_copilot_output_and_stops_at_the_completion_signal(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event(DEFAULT_COMPLETION_SIGNAL), _event("never")])

    result = NoCapsule(tmp_path, copilot(InMemorySessionStore()), executor=cli).run("go")

    assert result.completed and result.success
    assert "never" not in result.stdout
    assert cli.terminated


def test_docker_capsule_streams_copilot_output_and_stops_at_the_completion_signal(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event(DEFAULT_COMPLETION_SIGNAL), _event("never")])
    docker = FakeDocker(cli)
    capsule = DockerCapsule(
        tmp_path, copilot(InMemorySessionStore()), image_name="orb:test", container_uid=1000, docker=docker
    )

    result = capsule.run("go")
    capsule.close()

    assert result.completed and result.success
    assert "never" not in result.stdout
    assert cli.terminated


def test_docker_capsule_rejects_an_image_built_for_another_uid(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), image_user="999")

    with pytest.raises(OrbError, match="UID mismatch"):
        DockerCapsule(tmp_path, copilot(InMemorySessionStore()), image_name="orb:x", container_uid=1000, docker=docker)

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
    Mount,
    NoCapsule,
    OrbError,
    copilot,
)
from orb.testing import FakeAgentClient, FakeCopilotCli, FakeDocker


def _event(delta: str) -> str:
    return json.dumps({"type": "assistant.message_delta", "data": {"deltaContent": delta}})


def _flags(argv: tuple[str, ...]) -> set[str]:
    return set(argv[3:])


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
    assert f"{tmp_path}:{tmp_path}:z" in start and "A=1" in start and "2" in start
    assert start[-1] == "orb:test"
    container = start[start.index("--name") + 1]
    exec_prefix = ("docker", "exec", "-w", str(tmp_path), container)
    assert ("sh", "-c", "ls") == next(c for c in docker.calls if c[:5] == exec_prefix and "ls" in c)[5:]
    assert any(c[:5] == exec_prefix and c[5] == "copilot" for c in docker.calls)
    assert cli.calls[0][2] == "see listing" and result.success
    assert capsule.workspace == str(tmp_path)
    assert capsule.isolated is True
    assert ("docker", "stop", container) in docker.calls and ("docker", "rm", container) in docker.calls


def test_docker_capsule_mounts_only_the_harness_root_at_its_host_path(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: /host/repo/.git/worktrees/w\n")
    docker = FakeDocker(FakeCopilotCli())

    capsule = DockerCapsule(tmp_path, container_uid=1000, docker=docker)
    capsule.close()

    start = next(call for call in docker.calls if call[1:3] == ("run", "-d"))
    assert start.count("-v") == 1
    assert f"{tmp_path}:{tmp_path}:z" in start
    assert start[start.index("-w") + 1] == str(tmp_path)


def test_docker_capsule_derives_the_image_name_from_the_workspace_folder(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli())
    workspace = tmp_path / "My Repo!"

    capsule = DockerCapsule(workspace, container_uid=1000, docker=docker)
    capsule.close()

    start = next(call for call in docker.calls if call[1:3] == ("run", "-d"))
    assert start[-1] == "orb:my-repo-"


def test_docker_capsule_resolves_a_relative_user_mount_against_the_harness_root(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    docker = FakeDocker(FakeCopilotCli())

    capsule = DockerCapsule(tmp_path, container_uid=1000, docker=docker, mounts=[Mount(str(extra), "added")])
    capsule.close()

    start = next(call for call in docker.calls if call[1:3] == ("run", "-d"))
    assert f"{extra}:{tmp_path / 'added'}:z" in start


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


def test_docker_capsule_retries_a_transient_exit_code_then_starts_normally(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), run_results=[137, 0])
    sleeps: list[float] = []

    capsule = DockerCapsule(tmp_path, container_uid=1000, docker=docker, sleep=sleeps.append)
    capsule.close()

    run_calls = [call for call in docker.calls if call[1:3] == ("run", "-d")]
    assert len(run_calls) == 2
    assert sleeps == [0.25]
    assert sum(1 for call in docker.calls if call[1:3] == ("rm", "-f")) == 1


def test_docker_capsule_fails_after_exhausting_retries_on_a_transient_exit_code(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), run_results=[137, 137, 137])
    sleeps: list[float] = []

    with pytest.raises(CommandError, match="137"):
        DockerCapsule(tmp_path, container_uid=1000, docker=docker, sleep=sleeps.append)

    run_calls = [call for call in docker.calls if call[1:3] == ("run", "-d")]
    assert len(run_calls) == 3
    assert sleeps == [0.25, 0.25]
    assert sum(1 for call in docker.calls if call[1:3] == ("rm", "-f")) == 3


def test_docker_capsule_fails_at_once_on_a_non_transient_exit_code(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), run_results=[1])

    with pytest.raises(CommandError, match="1"):
        DockerCapsule(tmp_path, container_uid=1000, docker=docker)

    run_calls = [call for call in docker.calls if call[1:3] == ("run", "-d")]
    assert len(run_calls) == 1
    assert sum(1 for call in docker.calls if call[1:3] == ("rm", "-f")) == 1


def test_docker_capsule_fails_at_once_on_a_start_timeout_and_removes_the_container(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), run_results=[CommandError("docker run", None, "timed out after 60s")])

    with pytest.raises(CommandError, match="timed out"):
        DockerCapsule(tmp_path, container_uid=1000, docker=docker)

    run_calls = [call for call in docker.calls if call[1:3] == ("run", "-d")]
    assert len(run_calls) == 1
    assert sum(1 for call in docker.calls if call[1:3] == ("rm", "-f")) == 1


def test_docker_capsule_bounds_each_start_attempt_with_a_custom_timeout(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), run_results=[137, 0])

    capsule = DockerCapsule(
        tmp_path, container_uid=1000, docker=docker, start_timeout_s=5, sleep=lambda _: None
    )
    capsule.close()

    assert docker.run_timeouts == [5, 5]


def test_docker_capsule_run_carries_allow_all_and_no_allow_all_tools(tmp_path: Path) -> None:
    cli = FakeCopilotCli()
    capsule = DockerCapsule(tmp_path, container_uid=1000, docker=FakeDocker(cli))

    capsule.run(copilot(InMemorySessionStore()), "go")
    capsule.close()

    call_flags = _flags(cli.calls[0])
    assert "--allow-all" in call_flags
    assert "--allow-all-tools" not in call_flags


def test_no_capsule_run_carries_allow_all_tools_and_add_dir_for_the_harness_root_and_added_dirs(
    tmp_path: Path,
) -> None:
    cli = FakeCopilotCli()
    extra = tmp_path / "extra"

    NoCapsule(tmp_path, executor=cli).run(
        copilot(InMemorySessionStore()), "go", options=AgentOptions(add_dirs=(extra,))
    )

    argv = cli.calls[0]
    call_flags = _flags(argv)
    assert "--allow-all-tools" in call_flags and "--allow-all" not in call_flags
    add_dir_values = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--add-dir"]
    assert add_dir_values == [str(tmp_path), str(extra)]


def test_deny_rules_are_passed_on_both_a_host_and_a_container_capsule(tmp_path: Path) -> None:
    host_cli = FakeCopilotCli()
    NoCapsule(tmp_path, executor=host_cli).run(
        copilot(InMemorySessionStore()), "go", options=AgentOptions(deny_tools=("shell",))
    )
    container_cli = FakeCopilotCli()
    container_capsule = DockerCapsule(tmp_path, container_uid=1000, docker=FakeDocker(container_cli))

    container_capsule.run(copilot(InMemorySessionStore()), "go", options=AgentOptions(deny_tools=("shell",)))
    container_capsule.close()

    for cli in (host_cli, container_cli):
        argv = cli.calls[0]
        assert argv[argv.index("--deny-tool") + 1] == "shell"


def test_custom_agent_options_do_not_change_the_isolation_driven_permission_level(tmp_path: Path) -> None:
    cli = FakeCopilotCli()
    options = AgentOptions(extra_args=("--allow-all", "--no-color"))

    NoCapsule(tmp_path, executor=cli).run(copilot(InMemorySessionStore()), "go", options=options)

    argv = cli.calls[0]
    assert argv.count("--allow-all-tools") == 1


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

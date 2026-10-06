from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from loop import (
    AgentOptions,
    Cancelled,
    Sandbox,
    CommandError,
    DockerSandbox,
    InMemorySessionStore,
    Mount,
    NoSandbox,
    LoopError,
    copilot,
)
from loop.testing import FakeAgentClient, FakeCopilotCli, FakeDocker


_RESPONSE = '{"identifier": "t|1", "status": "completed", "result": {}}'


def _event(delta: str) -> str:
    return json.dumps({"type": "assistant.message_delta", "data": {"deltaContent": delta}})


def _flags(argv: tuple[str, ...]) -> set[str]:
    return set(argv[3:])


def test_no_sandbox_delegates_prompt_args_and_options_to_its_agent(tmp_path: Path) -> None:
    agent = FakeAgentClient(lambda prompt, options: f"{prompt}:{options.model}")

    with NoSandbox(tmp_path) as sandbox:
        result = sandbox.run(lambda executor: agent, "p=${{A}}", {"A": "1"}, AgentOptions(model="m"))

    assert isinstance(sandbox, Sandbox)
    assert sandbox.workspace == str(tmp_path)
    assert sandbox.isolated is False
    assert result.stdout == "p=1:m"


def test_no_sandbox_executes_commands_on_the_host_in_the_workspace(tmp_path: Path) -> None:
    sandbox = NoSandbox(tmp_path)

    assert sandbox.exec("pwd").strip() == str(tmp_path)
    assert sandbox.executor("echo hi && false").returncode == 1
    with pytest.raises(CommandError):
        sandbox.exec("exit 3")


def test_no_sandbox_runs_copilot_through_an_injected_executor(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: "done")

    result = NoSandbox(tmp_path, executor=cli).run(copilot(InMemorySessionStore()), "hi")

    assert result.stdout == "done" and cli.calls[0][2] == "hi"


def test_no_sandbox_runs_a_different_agent_on_each_run(tmp_path: Path) -> None:
    planner = FakeAgentClient(lambda prompt, options: "planned")
    reviewer = FakeAgentClient(lambda prompt, options: "reviewed")
    sandbox = NoSandbox(tmp_path)

    first = sandbox.run(lambda executor: planner, "plan")
    second = sandbox.run(lambda executor: reviewer, "review")

    assert (first.stdout, second.stdout) == ("planned", "reviewed")
    assert planner.calls[0][0] == "plan" and reviewer.calls[0][0] == "review"


def test_docker_sandbox_starts_a_container_and_runs_the_agent_inside_it(tmp_path: Path) -> None:
    cli = FakeCopilotCli(shell=lambda command: "listing\n")
    docker = FakeDocker(cli)
    sandbox = DockerSandbox(
        tmp_path,
        image_name="loop:test",
        container_uid=1000,
        env={"A": "1"},
        cpus=2,
        docker=docker,
    )

    result = sandbox.run(copilot(InMemorySessionStore()), "see !`ls`", options=AgentOptions(session_key="k"))
    sandbox.close()

    start = next(call for call in docker.calls if call[1:3] == ("run", "-d"))
    assert f"{tmp_path}:{tmp_path}:z" in start and "A=1" in start and "2" in start
    assert start[-1] == "loop:test"
    container = start[start.index("--name") + 1]
    exec_prefix = ("docker", "exec", "-w", str(tmp_path), container)
    assert ("sh", "-c", "ls") == next(c for c in docker.calls if c[:5] == exec_prefix and "ls" in c)[5:]
    assert any(c[:5] == exec_prefix and c[5] == "copilot" for c in docker.calls)
    assert cli.calls[0][2] == "see listing" and result.exit_code == 0
    assert sandbox.workspace == str(tmp_path)
    assert sandbox.isolated is True
    assert ("docker", "stop", container) in docker.calls and ("docker", "rm", container) in docker.calls


def test_docker_sandbox_mounts_only_the_harness_root_at_its_host_path(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("gitdir: /host/repo/.git/worktrees/w\n")
    docker = FakeDocker(FakeCopilotCli())

    sandbox = DockerSandbox(tmp_path, container_uid=1000, docker=docker)
    sandbox.close()

    start = next(call for call in docker.calls if call[1:3] == ("run", "-d"))
    assert start.count("-v") == 1
    assert f"{tmp_path}:{tmp_path}:z" in start
    assert start[start.index("-w") + 1] == str(tmp_path)


def test_docker_sandbox_derives_the_image_name_from_the_workspace_folder(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli())
    workspace = tmp_path / "My Repo!"

    sandbox = DockerSandbox(workspace, container_uid=1000, docker=docker)
    sandbox.close()

    start = next(call for call in docker.calls if call[1:3] == ("run", "-d"))
    assert start[-1] == "loop:my-repo-"


def test_docker_sandbox_resolves_a_relative_user_mount_against_the_harness_root(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    docker = FakeDocker(FakeCopilotCli())

    sandbox = DockerSandbox(tmp_path, container_uid=1000, docker=docker, mounts=[Mount(str(extra), "added")])
    sandbox.close()

    start = next(call for call in docker.calls if call[1:3] == ("run", "-d"))
    assert f"{extra}:{tmp_path / 'added'}:z" in start


def test_docker_sandbox_runs_a_different_agent_on_each_run(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(shell=lambda command: ""))
    sandbox = DockerSandbox(tmp_path, container_uid=1000, docker=docker)
    planner = FakeAgentClient(lambda prompt, options: "planned")
    reviewer = FakeAgentClient(lambda prompt, options: "reviewed")

    first = sandbox.run(lambda executor: planner, "plan")
    second = sandbox.run(lambda executor: reviewer, "review")
    sandbox.close()

    assert (first.stdout, second.stdout) == ("planned", "reviewed")
    assert planner.calls[0][0] == "plan" and reviewer.calls[0][0] == "review"


def test_no_sandbox_streams_copilot_output_and_parses_the_response_after_exit(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event(_RESPONSE), _event("trailing")])

    result = NoSandbox(tmp_path, executor=cli).run(copilot(InMemorySessionStore()), "go")

    assert result.success and json.loads(result.response)["identifier"] == "t|1"
    assert "trailing" in result.stdout
    assert not cli.terminated


def test_docker_sandbox_streams_copilot_output_and_parses_the_response_after_exit(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event(_RESPONSE), _event("trailing")])
    docker = FakeDocker(cli)
    sandbox = DockerSandbox(tmp_path, image_name="loop:test", container_uid=1000, docker=docker)

    result = sandbox.run(copilot(InMemorySessionStore()), "go")
    sandbox.close()

    assert result.success and json.loads(result.response)["status"] == "completed"
    assert "trailing" in result.stdout
    assert not cli.terminated


def test_docker_sandbox_rejects_an_image_built_for_another_uid(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), image_user="999")

    with pytest.raises(LoopError, match="UID mismatch"):
        DockerSandbox(tmp_path, image_name="loop:x", container_uid=1000, docker=docker)


def test_docker_sandbox_retries_a_transient_exit_code_then_starts_normally(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), run_results=[137, 0])
    sleeps: list[float] = []

    sandbox = DockerSandbox(tmp_path, container_uid=1000, docker=docker, sleep=sleeps.append)
    sandbox.close()

    run_calls = [call for call in docker.calls if call[1:3] == ("run", "-d")]
    assert len(run_calls) == 2
    assert sleeps == [0.25]
    assert sum(1 for call in docker.calls if call[1:3] == ("rm", "-f")) == 1


def test_docker_sandbox_fails_after_exhausting_retries_on_a_transient_exit_code(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), run_results=[137, 137, 137])
    sleeps: list[float] = []

    with pytest.raises(CommandError, match="137"):
        DockerSandbox(tmp_path, container_uid=1000, docker=docker, sleep=sleeps.append)

    run_calls = [call for call in docker.calls if call[1:3] == ("run", "-d")]
    assert len(run_calls) == 3
    assert sleeps == [0.25, 0.25]
    assert sum(1 for call in docker.calls if call[1:3] == ("rm", "-f")) == 3


def test_docker_sandbox_fails_at_once_on_a_non_transient_exit_code(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), run_results=[1])

    with pytest.raises(CommandError, match="1"):
        DockerSandbox(tmp_path, container_uid=1000, docker=docker)

    run_calls = [call for call in docker.calls if call[1:3] == ("run", "-d")]
    assert len(run_calls) == 1
    assert sum(1 for call in docker.calls if call[1:3] == ("rm", "-f")) == 1


def test_docker_sandbox_fails_at_once_on_a_start_timeout_and_removes_the_container(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), run_results=[CommandError("docker run", None, "timed out after 60s")])

    with pytest.raises(CommandError, match="timed out"):
        DockerSandbox(tmp_path, container_uid=1000, docker=docker)

    run_calls = [call for call in docker.calls if call[1:3] == ("run", "-d")]
    assert len(run_calls) == 1
    assert sum(1 for call in docker.calls if call[1:3] == ("rm", "-f")) == 1


def test_docker_sandbox_bounds_each_start_attempt_with_a_custom_timeout(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli(), run_results=[137, 0])

    sandbox = DockerSandbox(
        tmp_path, container_uid=1000, docker=docker, start_timeout_s=5, sleep=lambda _: None
    )
    sandbox.close()

    assert docker.run_timeouts == [5, 5]


def test_docker_sandbox_run_carries_allow_all_and_no_allow_all_tools(tmp_path: Path) -> None:
    cli = FakeCopilotCli()
    sandbox = DockerSandbox(tmp_path, container_uid=1000, docker=FakeDocker(cli))

    sandbox.run(copilot(InMemorySessionStore()), "go")
    sandbox.close()

    call_flags = _flags(cli.calls[0])
    assert "--allow-all" in call_flags
    assert "--allow-all-tools" not in call_flags


def test_no_sandbox_run_carries_allow_all_tools_and_add_dir_for_the_harness_root_and_added_dirs(
    tmp_path: Path,
) -> None:
    cli = FakeCopilotCli()
    extra = tmp_path / "extra"

    NoSandbox(tmp_path, executor=cli).run(
        copilot(InMemorySessionStore()), "go", options=AgentOptions(add_dirs=(extra,))
    )

    argv = cli.calls[0]
    call_flags = _flags(argv)
    assert "--allow-all-tools" in call_flags and "--allow-all" not in call_flags
    add_dir_values = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--add-dir"]
    assert add_dir_values == [str(tmp_path), str(extra)]


def test_deny_rules_are_passed_on_both_a_host_and_a_container_sandbox(tmp_path: Path) -> None:
    host_cli = FakeCopilotCli()
    NoSandbox(tmp_path, executor=host_cli).run(
        copilot(InMemorySessionStore()), "go", options=AgentOptions(deny_tools=("shell",))
    )
    container_cli = FakeCopilotCli()
    container_sandbox = DockerSandbox(tmp_path, container_uid=1000, docker=FakeDocker(container_cli))

    container_sandbox.run(copilot(InMemorySessionStore()), "go", options=AgentOptions(deny_tools=("shell",)))
    container_sandbox.close()

    for cli in (host_cli, container_cli):
        argv = cli.calls[0]
        assert argv[argv.index("--deny-tool") + 1] == "shell"


def test_custom_agent_options_do_not_change_the_isolation_driven_permission_level(tmp_path: Path) -> None:
    cli = FakeCopilotCli()
    options = AgentOptions(extra_args=("--allow-all", "--no-color"))

    NoSandbox(tmp_path, executor=cli).run(copilot(InMemorySessionStore()), "go", options=options)

    argv = cli.calls[0]
    assert argv.count("--allow-all-tools") == 1


def test_no_sandbox_closing_twice_is_a_no_op(tmp_path: Path) -> None:
    sandbox = NoSandbox(tmp_path)

    sandbox.close()
    sandbox.close()


def test_docker_sandbox_closing_twice_stops_and_removes_the_container_once(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli())
    sandbox = DockerSandbox(tmp_path, container_uid=1000, docker=docker)

    sandbox.close()
    sandbox.close()

    assert sum(1 for call in docker.calls if call[1] == "stop") == 1
    assert sum(1 for call in docker.calls if call[1] == "rm") == 1


def test_no_sandbox_cancels_an_in_flight_agent_run_and_reports_cancelled(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event("never")])
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(Cancelled):
        NoSandbox(tmp_path, executor=cli, cancel=cancel).run(copilot(InMemorySessionStore()), "go")

    assert cli.terminated


def test_no_sandbox_without_cancel_runs_the_agent_as_before(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: "done")

    result = NoSandbox(tmp_path, executor=cli).run(copilot(InMemorySessionStore()), "go")

    assert result.stdout == "done" and not cli.terminated


def test_docker_sandbox_cancels_an_in_flight_start_and_leaves_no_container(tmp_path: Path) -> None:
    docker = FakeDocker(FakeCopilotCli())
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(Cancelled):
        DockerSandbox(tmp_path, container_uid=1000, docker=docker, cancel=cancel)

    run_calls = [call for call in docker.calls if call[1:3] == ("run", "-d")]
    assert len(run_calls) == 1
    assert sum(1 for call in docker.calls if call[1:3] == ("rm", "-f")) == 1


def test_docker_sandbox_cancels_an_in_flight_agent_run_terminates_and_closes(tmp_path: Path) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("working"), _event("never")])
    docker = FakeDocker(cli)
    cancel = threading.Event()
    sandbox = DockerSandbox(tmp_path, container_uid=1000, docker=docker, cancel=cancel)
    cancel.set()

    with pytest.raises(Cancelled):
        sandbox.run(copilot(InMemorySessionStore()), "go")

    assert cli.terminated
    start = next(call for call in docker.calls if call[1:3] == ("run", "-d"))
    container = start[start.index("--name") + 1]
    assert ("docker", "rm", "-f", container) in docker.calls

    sandbox.close()
    assert sum(1 for call in docker.calls if call[1:3] == ("rm", "-f")) == 1

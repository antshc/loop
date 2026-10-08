from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from loop import (
    AgentBinding,
    AgentOptions,
    AgentResult,
    AgentSession,
    CommandResult,
    FileSessionStore,
    InMemorySessionStore,
    copilot,
)
from loop.testing import FakeCopilotCli

WORKSPACE = "/harness"


def flags(argv: tuple[str, ...]) -> set[str]:
    return set(argv[3:])


def binding(executor, *, workspace: str = WORKSPACE) -> AgentBinding:
    return AgentBinding(executor, workspace)


def _event(delta: str) -> str:
    return json.dumps({"type": "assistant.message_delta", "data": {"deltaContent": delta}})


def _envelope(identifier: str = "t|1", status: str = "completed", result: dict | None = None) -> str:
    return json.dumps({"identifier": identifier, "status": status, "result": result if result is not None else {}})


def test_run_without_session_key_is_a_fresh_invocation() -> None:
    cli = FakeCopilotCli(lambda prompt: f"echo:{prompt}")
    sessions = InMemorySessionStore()

    result = copilot(sessions)(binding(cli)).run(
        "hi {{A}}", {"A": "1"}, AgentOptions(model="m", add_dirs=(Path("/d"),))
    )

    assert result == AgentResult("echo:hi 1", "", 0)
    assert cli.calls[0][:4] == ("copilot", "-p", "hi 1", "--output-format")
    assert {"--model", "m", "--add-dir", "/d"} <= flags(cli.calls[0])
    assert not any(arg == "--name" or arg.startswith("--resume") for arg in cli.calls[0])


def test_a_run_carries_allow_all_tools_and_add_dir_for_the_workspace_and_no_allow_all() -> None:
    cli = FakeCopilotCli()

    copilot(InMemorySessionStore())(binding(cli, workspace=WORKSPACE)).run(
        "go", options=AgentOptions(add_dirs=(Path("/extra"),))
    )

    argv = cli.calls[0]
    call_flags = flags(argv)
    assert "--allow-all-tools" in call_flags and "--allow-all" not in call_flags
    assert argv[argv.index("--add-dir") + 1] == WORKSPACE
    assert "/extra" in call_flags


def test_deny_rules_are_rendered_as_deny_tool() -> None:
    cli = FakeCopilotCli()

    copilot(InMemorySessionStore())(binding(cli)).run(
        "go", options=AgentOptions(deny_tools=("shell", "write"))
    )

    argv = cli.calls[0]
    deny_indexes = [i for i, arg in enumerate(argv) if arg == "--deny-tool"]
    assert [argv[i + 1] for i in deny_indexes] == ["shell", "write"]


def test_custom_extra_args_do_not_change_the_permission_level() -> None:
    cli = FakeCopilotCli()

    copilot(InMemorySessionStore())(binding(cli)).run(
        "go", options=AgentOptions(extra_args=("--allow-all", "--no-color"))
    )

    argv = cli.calls[0]
    assert argv.count("--allow-all-tools") == 1
    assert argv.index("--allow-all-tools") < argv.index("--allow-all")


def test_session_is_created_with_prefix_saved_after_the_run_and_resumed_by_key() -> None:
    cli = FakeCopilotCli(lambda prompt: _event(_envelope("issue-42")))
    sessions = InMemorySessionStore()
    client = copilot(sessions)(binding(cli))
    options = AgentOptions(session_key="issue-42", session_name_prefix="loop-")

    client.run("one", options=options)
    client.run("two", options=options)

    first, second = cli.calls
    assert first[first.index("--name") + 1] == "loop-issue-42"
    assert "--resume=loop-issue-42" in second and "--name" not in second
    assert sessions.get("issue-42") == AgentSession("issue-42", "loop-issue-42")


def test_failed_run_keeps_stderr_and_exit_code_and_does_not_record_the_session() -> None:
    cli = FakeCopilotCli(lambda prompt: CommandResult(2, _event("partial"), "boom"))
    sessions = InMemorySessionStore()

    result = copilot(sessions)(binding(cli)).run("go", options=AgentOptions(session_key="k"))

    assert (result.stdout, result.stderr, result.exit_code) == ("partial", "boom", 2)
    assert not result.success and result.output == "partialboom"
    assert sessions.get("k") is None


def test_assistant_text_is_the_concatenation_of_message_delta_events() -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("Hel"), _event("lo"), _event(" world")])

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert result.stdout == "Hello world"


def test_the_last_response_object_is_extracted_as_the_response_and_marks_success() -> None:
    envelope = _envelope("t|1", result={"commit": "abc"})
    cli = FakeCopilotCli(lambda prompt: [_event("working on it"), _event(envelope)])

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert result.success
    assert result.response == envelope


def test_an_earlier_unrelated_json_object_is_ignored_and_the_later_response_object_wins() -> None:
    first = _envelope("t|1", result={"commit": "old"})
    second = _envelope("t|1", result={"commit": "new"})
    cli = FakeCopilotCli(lambda prompt: [_event(first), _event("more work"), _event(second)])

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert result.response == second


def test_a_failed_status_is_not_successful_but_its_response_is_kept() -> None:
    envelope = _envelope("t|1", status="failed", result={"reason": "tests did not pass"})
    cli = FakeCopilotCli(lambda prompt: _event(envelope))

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert not result.success
    assert result.response == envelope


def test_no_response_object_is_an_error_with_an_empty_response() -> None:
    cli = FakeCopilotCli(lambda prompt: _event("just some assistant text, no envelope"))

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert not result.success
    assert result.response == ""


def test_malformed_response_json_is_an_error() -> None:
    cli = FakeCopilotCli(lambda prompt: _event('{"identifier": "t|1", "status": '))

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert not result.success
    assert result.response == ""


def test_a_nonzero_exit_with_a_completed_envelope_is_still_unsuccessful() -> None:
    envelope = _envelope("t|1")
    cli = FakeCopilotCli(lambda prompt: CommandResult(1, _event(envelope), "boom"))

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert not result.success
    assert result.response == envelope


def test_the_process_is_not_terminated_early_when_the_agent_keeps_running_after_its_response() -> None:
    envelope = _envelope("t|1")
    lines = [_event("working"), _event(envelope), _event("still cleaning up"), _event("done")]
    cli = FakeCopilotCli(lambda prompt: "\n".join(lines))

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert result.success
    assert "still cleaning up" in result.stdout and "done" in result.stdout
    assert not cli.terminated


def test_each_output_line_is_logged_live(caplog: pytest.LogCaptureFixture) -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("one"), _event("two")])

    with caplog.at_level(logging.INFO, logger="loop.agents.copilot"):
        copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert any("one" in record.message for record in caplog.records)
    assert any("two" in record.message for record in caplog.records)


def test_agent_option_selects_the_named_agent_and_is_omitted_when_unset() -> None:
    cli = FakeCopilotCli()
    client = copilot(InMemorySessionStore())(binding(cli))

    client.run("a", options=AgentOptions(agent="reviewer"))
    client.run("b")

    first, second = cli.calls
    assert first[first.index("--agent") + 1] == "reviewer"
    assert "--agent" not in second


def test_template_commands_run_through_the_run_executor() -> None:
    cli = FakeCopilotCli(shell=lambda command: "main\n")

    copilot(InMemorySessionStore())(binding(cli)).run("on !`git branch --show-current`")

    assert cli.shell_commands == ["git branch --show-current"]
    assert cli.calls[0][2] == "on main"


def test_file_session_store_survives_a_new_instance(tmp_path: Path) -> None:
    FileSessionStore(tmp_path).save(AgentSession("k", "loop-k"))

    assert FileSessionStore(tmp_path).get("k") == AgentSession("k", "loop-k")
    assert FileSessionStore(tmp_path).get("other") is None

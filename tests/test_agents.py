from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from orb import (
    DEFAULT_COMPLETION_SIGNAL,
    AgentOptions,
    AgentResult,
    AgentSession,
    CapsuleBinding,
    CommandResult,
    FileSessionStore,
    InMemorySessionStore,
    copilot,
    dry_run,
)
from orb.testing import FakeCopilotCli

WORKSPACE = "/harness"


def flags(argv: tuple[str, ...]) -> set[str]:
    return set(argv[3:])


def binding(executor, *, isolated: bool = False, workspace: str = WORKSPACE) -> CapsuleBinding:
    return CapsuleBinding(executor, isolated, workspace)


def _event(delta: str) -> str:
    return json.dumps({"type": "assistant.message_delta", "data": {"deltaContent": delta}})


def test_run_without_session_key_is_a_fresh_invocation() -> None:
    cli = FakeCopilotCli(lambda prompt: f"echo:{prompt}")
    sessions = InMemorySessionStore()

    result = copilot(sessions)(binding(cli)).run(
        "hi ${{A}}", {"A": "1"}, AgentOptions(model="m", add_dirs=(Path("/d"),))
    )

    assert result == AgentResult("echo:hi 1", "", 0)
    assert not result.completed
    assert cli.calls[0][:4] == ("copilot", "-p", "hi 1", "--output-format")
    assert {"--model", "m", "--add-dir", "/d"} <= flags(cli.calls[0])
    assert not any(arg == "--name" or arg.startswith("--resume") for arg in cli.calls[0])


def test_a_container_run_carries_allow_all_and_no_allow_all_tools() -> None:
    cli = FakeCopilotCli()

    copilot(InMemorySessionStore())(binding(cli, isolated=True)).run("go")

    call_flags = flags(cli.calls[0])
    assert "--allow-all" in call_flags
    assert "--allow-all-tools" not in call_flags


def test_a_host_run_carries_allow_all_tools_and_add_dir_for_the_workspace_and_no_allow_all() -> None:
    cli = FakeCopilotCli()

    copilot(InMemorySessionStore())(binding(cli, isolated=False, workspace=WORKSPACE)).run(
        "go", options=AgentOptions(add_dirs=(Path("/extra"),))
    )

    argv = cli.calls[0]
    call_flags = flags(argv)
    assert "--allow-all-tools" in call_flags and "--allow-all" not in call_flags
    assert argv[argv.index("--add-dir") + 1] == WORKSPACE
    assert "/extra" in call_flags


def test_deny_rules_are_rendered_as_deny_tool_on_both_isolation_levels() -> None:
    for isolated in (True, False):
        cli = FakeCopilotCli()

        copilot(InMemorySessionStore())(binding(cli, isolated=isolated)).run(
            "go", options=AgentOptions(deny_tools=("shell", "write"))
        )

        argv = cli.calls[0]
        deny_indexes = [i for i, arg in enumerate(argv) if arg == "--deny-tool"]
        assert [argv[i + 1] for i in deny_indexes] == ["shell", "write"]


def test_custom_extra_args_do_not_change_the_isolation_driven_permission_level() -> None:
    cli = FakeCopilotCli()

    copilot(InMemorySessionStore())(binding(cli, isolated=False)).run(
        "go", options=AgentOptions(extra_args=("--allow-all", "--no-color"))
    )

    argv = cli.calls[0]
    assert argv.count("--allow-all-tools") == 1
    assert argv.index("--allow-all-tools") < argv.index("--allow-all")


def test_session_is_created_with_prefix_saved_after_the_run_and_resumed_by_key() -> None:
    cli = FakeCopilotCli()
    sessions = InMemorySessionStore()
    client = copilot(sessions)(binding(cli))
    options = AgentOptions(session_key="issue-42", session_name_prefix="orb-")

    client.run("one", options=options)
    client.run("two", options=options)

    first, second = cli.calls
    assert first[first.index("--name") + 1] == "orb-issue-42"
    assert "--resume=orb-issue-42" in second and "--name" not in second
    assert sessions.get("issue-42") == AgentSession("issue-42", "orb-issue-42")


def test_failed_run_keeps_stderr_and_exit_code_and_does_not_record_the_session() -> None:
    cli = FakeCopilotCli(lambda prompt: CommandResult(2, _event("partial"), "boom"))
    sessions = InMemorySessionStore()

    result = copilot(sessions)(binding(cli)).run("go", options=AgentOptions(session_key="k"))

    assert (result.stdout, result.stderr, result.exit_code) == ("partial", "boom", 2)
    assert not result.success and not result.completed and result.output == "partialboom"
    assert sessions.get("k") is None


def test_assistant_text_is_the_concatenation_of_message_delta_events() -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("Hel"), _event("lo"), _event(" world")])

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert result.stdout == "Hello world"


def test_a_report_printed_before_the_signal_is_kept_in_the_result() -> None:
    delta = f"REPORT: all good\n{DEFAULT_COMPLETION_SIGNAL}"
    cli = FakeCopilotCli(lambda prompt: delta)

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert result.completed and result.success
    assert "REPORT: all good" in result.stdout


def test_the_completion_signal_stops_the_process_and_marks_the_result_completed_and_successful() -> None:
    lines = [_event("working"), _event(DEFAULT_COMPLETION_SIGNAL), _event("never seen")]
    cli = FakeCopilotCli(lambda prompt: CommandResult(143, "\n".join(lines), ""))

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert result.completed and result.success
    assert "never seen" not in result.stdout
    assert cli.terminated


def test_agent_option_selects_the_named_agent_and_is_omitted_when_unset() -> None:
    cli = FakeCopilotCli()
    client = copilot(InMemorySessionStore())(binding(cli))

    client.run("a", options=AgentOptions(agent="reviewer"))
    client.run("b")

    first, second = cli.calls
    assert first[first.index("--agent") + 1] == "reviewer"
    assert "--agent" not in second


def test_afk_dry_run_env_var_has_no_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFK_DRY_RUN", "1")
    cli = FakeCopilotCli(lambda prompt: "hi")

    result = copilot(InMemorySessionStore())(binding(cli)).run("go")

    assert cli.calls and result.stdout == "hi"


def test_dry_run_agent_client_logs_the_rendered_prompt_and_never_starts_a_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cli = FakeCopilotCli()
    sessions = InMemorySessionStore()

    with caplog.at_level(logging.INFO, logger="orb.agents.dry_run"):
        result = dry_run(sessions)(binding(cli)).run("hi ${{A}}", {"A": "1"})

    assert result == AgentResult("", "", 0)
    assert "hi 1" in caplog.text
    assert cli.calls == [] and cli.shell_commands == []


def test_template_commands_run_through_the_capsule_executor() -> None:
    cli = FakeCopilotCli(shell=lambda command: "main\n")

    copilot(InMemorySessionStore())(binding(cli)).run("on !`git branch --show-current`")

    assert cli.shell_commands == ["git branch --show-current"]
    assert cli.calls[0][2] == "on main"


def test_file_session_store_survives_a_new_instance(tmp_path: Path) -> None:
    FileSessionStore(tmp_path).save(AgentSession("k", "orb-k"))

    assert FileSessionStore(tmp_path).get("k") == AgentSession("k", "orb-k")
    assert FileSessionStore(tmp_path).get("other") is None

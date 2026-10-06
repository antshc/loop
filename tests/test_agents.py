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
    CommandResult,
    FileSessionStore,
    InMemorySessionStore,
    copilot,
    dry_run,
)
from orb.testing import FakeCopilotCli


def flags(argv: tuple[str, ...]) -> set[str]:
    return set(argv[3:])


def _event(delta: str) -> str:
    return json.dumps({"type": "assistant.message_delta", "data": {"deltaContent": delta}})


def test_run_without_session_key_is_a_fresh_invocation() -> None:
    cli = FakeCopilotCli(lambda prompt: f"echo:{prompt}")
    sessions = InMemorySessionStore()

    result = copilot(sessions)(cli).run("hi ${{A}}", {"A": "1"}, AgentOptions(model="m", add_dirs=(Path("/d"),)))

    assert result == AgentResult("echo:hi 1", "", 0)
    assert not result.completed
    assert cli.calls[0][:4] == ("copilot", "-p", "hi 1", "--output-format")
    assert {"--model", "m", "--add-dir", "/d"} <= flags(cli.calls[0])
    assert not any(arg == "--name" or arg.startswith("--resume") for arg in cli.calls[0])


def test_session_is_created_with_prefix_saved_after_the_run_and_resumed_by_key() -> None:
    cli = FakeCopilotCli()
    sessions = InMemorySessionStore()
    client = copilot(sessions)(cli)
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

    result = copilot(sessions)(cli).run("go", options=AgentOptions(session_key="k"))

    assert (result.stdout, result.stderr, result.exit_code) == ("partial", "boom", 2)
    assert not result.success and not result.completed and result.output == "partialboom"
    assert sessions.get("k") is None


def test_assistant_text_is_the_concatenation_of_message_delta_events() -> None:
    cli = FakeCopilotCli(lambda prompt: [_event("Hel"), _event("lo"), _event(" world")])

    result = copilot(InMemorySessionStore())(cli).run("go")

    assert result.stdout == "Hello world"


def test_a_report_printed_before_the_signal_is_kept_in_the_result() -> None:
    delta = f"REPORT: all good\n{DEFAULT_COMPLETION_SIGNAL}"
    cli = FakeCopilotCli(lambda prompt: delta)

    result = copilot(InMemorySessionStore())(cli).run("go")

    assert result.completed and result.success
    assert "REPORT: all good" in result.stdout


def test_the_completion_signal_stops_the_process_and_marks_the_result_completed_and_successful() -> None:
    lines = [_event("working"), _event(DEFAULT_COMPLETION_SIGNAL), _event("never seen")]
    cli = FakeCopilotCli(lambda prompt: CommandResult(143, "\n".join(lines), ""))

    result = copilot(InMemorySessionStore())(cli).run("go")

    assert result.completed and result.success
    assert "never seen" not in result.stdout
    assert cli.terminated


def test_agent_option_selects_the_named_agent_and_is_omitted_when_unset() -> None:
    cli = FakeCopilotCli()
    client = copilot(InMemorySessionStore())(cli)

    client.run("a", options=AgentOptions(agent="reviewer"))
    client.run("b")

    first, second = cli.calls
    assert first[first.index("--agent") + 1] == "reviewer"
    assert "--agent" not in second


def test_afk_dry_run_env_var_has_no_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFK_DRY_RUN", "1")
    cli = FakeCopilotCli(lambda prompt: "hi")

    result = copilot(InMemorySessionStore())(cli).run("go")

    assert cli.calls and result.stdout == "hi"


def test_dry_run_agent_client_logs_the_rendered_prompt_and_never_starts_a_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cli = FakeCopilotCli()
    sessions = InMemorySessionStore()

    with caplog.at_level(logging.INFO, logger="orb.agents.dry_run"):
        result = dry_run(sessions)(cli).run("hi ${{A}}", {"A": "1"})

    assert result == AgentResult("", "", 0)
    assert "hi 1" in caplog.text
    assert cli.calls == [] and cli.shell_commands == []


def test_template_commands_run_through_the_capsule_executor() -> None:
    cli = FakeCopilotCli(shell=lambda command: "main\n")

    copilot(InMemorySessionStore())(cli).run("on !`git branch --show-current`")

    assert cli.shell_commands == ["git branch --show-current"]
    assert cli.calls[0][2] == "on main"


def test_file_session_store_survives_a_new_instance(tmp_path: Path) -> None:
    FileSessionStore(tmp_path).save(AgentSession("k", "orb-k"))

    assert FileSessionStore(tmp_path).get("k") == AgentSession("k", "orb-k")
    assert FileSessionStore(tmp_path).get("other") is None

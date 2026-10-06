from __future__ import annotations

from pathlib import Path

from orb import (
    AgentOptions,
    AgentResult,
    AgentSession,
    CommandResult,
    FakeCopilotCli,
    FileSessionStore,
    InMemorySessionStore,
    copilot,
)


def flags(argv: tuple[str, ...]) -> set[str]:
    return set(argv[3:])


def test_run_without_session_key_is_a_fresh_invocation() -> None:
    cli = FakeCopilotCli(lambda prompt: f"echo:{prompt}")
    sessions = InMemorySessionStore()

    result = copilot(sessions)(cli).run("hi ${{A}}", {"A": "1"}, AgentOptions(model="m", add_dirs=(Path("/d"),)))

    assert result == AgentResult("echo:hi 1", "", 0)
    assert cli.calls[0][:4] == ("copilot", "-p", "hi 1", "--silent")
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
    cli = FakeCopilotCli(lambda prompt: CommandResult(2, "partial", "boom"))
    sessions = InMemorySessionStore()

    result = copilot(sessions)(cli).run("go", options=AgentOptions(session_key="k"))

    assert (result.stdout, result.stderr, result.exit_code) == ("partial", "boom", 2)
    assert not result.success and result.output == "partialboom"
    assert sessions.get("k") is None


def test_template_commands_run_through_the_capsule_executor() -> None:
    cli = FakeCopilotCli(shell=lambda command: "main\n")

    copilot(InMemorySessionStore())(cli).run("on !`git branch --show-current`")

    assert cli.shell_commands == ["git branch --show-current"]
    assert cli.calls[0][2] == "on main"


def test_file_session_store_survives_a_new_instance(tmp_path: Path) -> None:
    FileSessionStore(tmp_path).save(AgentSession("k", "orb-k"))

    assert FileSessionStore(tmp_path).get("k") == AgentSession("k", "orb-k")
    assert FileSessionStore(tmp_path).get("other") is None

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Iterator
from io import StringIO
from pathlib import Path

import pytest

from loop import (
    AgentCliHookWiring,
    AgentContext,
    AgentProfile,
    AgentRequest,
    CliOutcome,
    GitCli,
    NativeHandle,
    ProcessCliRunner,
    RunContext,
    SessionName,
    Start,
    configure_logging,
)


@pytest.fixture(autouse=True)
def restore_loggers() -> Iterator[None]:
    saved = [
        (logger, list(logger.handlers), logger.level, logger.propagate)
        for logger in (logging.getLogger(), logging.getLogger("loop"))
    ]
    yield
    for logger, handlers, level, propagate in saved:
        for handler in logger.handlers:
            if handler not in handlers:
                handler.close()
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate


def _emit_info_and_debug() -> None:
    logger = logging.getLogger("loop.test")
    logger.debug("detail")
    logger.info("summary")


def _records(log_file: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in log_file.read_text().splitlines()]


def test_info_logs_to_the_console_only(tmp_path: Path) -> None:
    log_file = tmp_path / "loop.log"
    console = StringIO()

    configure_logging("INFO", log_file=log_file, stream=console)
    _emit_info_and_debug()

    assert "summary" in console.getvalue()
    assert "detail" not in console.getvalue()
    assert not log_file.exists()


def test_debug_logs_info_and_debug_to_the_console_and_the_json_file(tmp_path: Path) -> None:
    log_file = tmp_path / "nested" / "loop.log"
    console = StringIO()

    configure_logging("DEBUG", log_file=log_file, stream=console)
    _emit_info_and_debug()

    assert "summary" in console.getvalue() and "detail" in console.getvalue()
    assert [record["message"] for record in _records(log_file)] == ["detail", "summary"]


def test_debug_without_a_log_file_writes_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    console = StringIO()

    configure_logging("DEBUG", log_file=None, stream=console)
    _emit_info_and_debug()

    assert "detail" in console.getvalue()
    assert list(tmp_path.iterdir()) == []


def test_an_unknown_level_falls_back_to_info(tmp_path: Path) -> None:
    console = StringIO()

    configure_logging("LOUD", log_file=tmp_path / "loop.log", stream=console)
    _emit_info_and_debug()

    assert "summary" in console.getvalue() and "detail" not in console.getvalue()


def test_repeated_configuration_does_not_duplicate_output() -> None:
    console = StringIO()

    configure_logging("INFO", stream=StringIO())
    configure_logging("INFO", stream=console)
    _emit_info_and_debug()

    assert console.getvalue().count("summary") == 1


def test_the_default_scope_leaves_the_root_logger_and_other_loggers_alone() -> None:
    root = logging.getLogger()
    before = (list(root.handlers), root.level)
    console = StringIO()

    configure_logging("DEBUG", log_file=None, stream=console)
    logging.getLogger("workflow.other").warning("app record")

    assert (list(root.handlers), root.level) == before
    assert "app record" not in console.getvalue()


def test_an_empty_logger_name_configures_the_root_so_the_whole_application_is_covered() -> None:
    console = StringIO()

    configure_logging("INFO", stream=console, logger="")
    logging.getLogger("workflow.other").info("app record")

    assert "app record" in console.getvalue()


def test_a_named_scope_does_not_duplicate_records_through_the_root() -> None:
    root_console, loop_console = StringIO(), StringIO()
    logging.getLogger().addHandler(logging.StreamHandler(root_console))

    configure_logging("INFO", stream=loop_console)
    _emit_info_and_debug()

    assert "summary" in loop_console.getvalue() and root_console.getvalue() == ""


class _Cli:
    name = "fake"
    hook_points = frozenset()

    def hook_wiring(self, *args: object) -> AgentCliHookWiring:
        return AgentCliHookWiring()

    def command(self, *args: object) -> list[str]:
        return ["fake-cli", "secret-prompt"]

    def parse(self, stdout: str, turn: Start, exit_code: int) -> CliOutcome:
        return CliOutcome(stdout, NativeHandle("fake", "h"), exit_code)


def _completed(stdout: str = "out") -> object:
    return type("Completed", (), {"stdout": stdout, "stderr": "", "returncode": 0})()


def test_a_cli_run_logs_start_and_finish_at_info_and_the_command_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = ProcessCliRunner(run=lambda *args, **kwargs: _completed())
    context = RunContext(AgentContext(cwd=Path("/repo")))

    with caplog.at_level(logging.DEBUG, logger="loop"):
        runner.run(AgentProfile(_Cli()), AgentRequest("x"), Start(SessionName("s")), context)

    info = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    debug = [record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG]
    assert info == ["agent run started: cli=fake session=s cwd=/repo", "agent run finished: cli=fake exit_code=0"]
    assert any("secret-prompt" in message for message in debug)


def test_the_command_is_not_logged_at_info(caplog: pytest.LogCaptureFixture) -> None:
    runner = ProcessCliRunner(run=lambda *args, **kwargs: _completed())

    with caplog.at_level(logging.INFO, logger="loop"):
        runner.run(AgentProfile(_Cli()), AgentRequest("x"), Start(SessionName("s")), RunContext(AgentContext(cwd=Path("/repo"))))

    assert "secret-prompt" not in caplog.text


def test_git_logs_worktree_changes_at_info_and_every_command_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "", "")

    with caplog.at_level(logging.DEBUG, logger="loop"):
        GitCli(run=fake).add_worktree(Path("/repo"), Path("/repo/.wt/a"), "a")

    info = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    debug = [record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG]
    assert info == ["adding worktree /repo/.wt/a on branch a"]
    assert any("check-ref-format" in message for message in debug)

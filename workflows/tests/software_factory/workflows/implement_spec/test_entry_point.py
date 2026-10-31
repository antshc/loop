"""The implement_spec command line: options, exit codes and the recorded fake run's tracker writes."""

from __future__ import annotations

import json
import signal
from pathlib import Path
from subprocess import CalledProcessError

import pytest
from loop import LoopHookError, LoopHookPoint, WorktreeError

from workflows.software_factory.workflows.implement_spec import __main__ as entry
from workflows.software_factory.workflows.implement_spec import dry
from workflows.software_factory.workflows.implement_spec.__main__ import main

from .harness import ImplementSpecHarness

SCENARIO = Path(__file__).with_name("fake_run_tracker_writes.json")

FAKE_ARGUMENTS = [
    "--no-otel",
    "--fake-speed",
    "0",
    *(argument for seam in dry.SEAMS for argument in ("--fake", seam)),
]

_NOT_WRITES = (("pr", "diff"), ("pr", "list"))


def record_tracker_writes(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path
) -> tuple[int, list[list[str]]]:
    """Runs the command with the agent, Git, GitHub and store faked; returns the exit code and every tracker write in order."""
    writes: list[list[str]] = []

    class RecordingGh(dry.DryGh):
        def __call__(self, args: tuple[str, ...]) -> str:
            if (
                args[0] in ("issue", "pr")
                and args[:2] not in _NOT_WRITES
                or args[:2] == ("api", "--method")
            ):
                writes.append(list(args))
            return super().__call__(args)

    monkeypatch.setattr(dry, "DryGh", RecordingGh)
    return main(["--log-dir", str(log_dir), *FAKE_ARGUMENTS]), writes


def test_the_help_exits_0_and_lists_no_visualize_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given the --help option, when the command starts, then it exits 0 and lists no --visualize option."""
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    help_text = capsys.readouterr().out
    assert exit_info.value.code == 0
    assert (
        "--spec" in help_text
        and "--fake-speed" in help_text
        and "--visualize" not in help_text
    )


def test_a_faked_git_without_a_faked_agent_is_refused() -> None:
    """Given --fake git alone, when the command starts, then it exits with a usage error."""
    with pytest.raises(SystemExit) as exit_info:
        main(["--fake", "git"])

    assert exit_info.value.code == 2


def test_the_recorded_fake_run_writes_to_the_tracker_exactly_as_the_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given the fake agent, Git, GitHub and store, when the command runs, then its tracker writes equal the golden."""
    code, writes = record_tracker_writes(monkeypatch, tmp_path)

    assert code == 0
    assert (json.dumps(writes, indent=2) + "\n").encode() == SCENARIO.read_bytes()


def test_a_dry_run_with_telemetry_off_delivers_the_scripted_spec_and_exits_0(
    tmp_path: Path,
) -> None:
    """Given --dry, when the command runs with telemetry off, then the scripted Spec is delivered, including the Ticket whose first attempt fails."""
    assert (
        main(["--log-dir", str(tmp_path), "--dry", "--no-otel", "--fake-speed", "0"])
        == 0
    )


def test_a_fake_agent_that_never_fails_a_first_attempt_runs_without_rework(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Given --no-rework, when the fake run completes, then it exits 0 and no attempt failed."""
    code = main(["--log-dir", str(tmp_path), "--no-rework", *FAKE_ARGUMENTS])

    assert code == 0 and " · fail" not in capsys.readouterr().err


def test_the_log_dir_holds_the_log_file_at_debug_level(tmp_path: Path) -> None:
    """Given --log-level DEBUG, when the command runs, then the log file lands in the --log-dir folder."""
    custom = tmp_path / "custom-logs"

    code = main(["--log-dir", str(custom), "--log-level", "DEBUG", *FAKE_ARGUMENTS])

    assert code == 0 and (custom / "implement_spec.log").is_file()


def test_the_console_shows_one_line_per_finished_span(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Given a fake run, when it completes, then the console carries the span lines, such as the Ticket whose first attempt failed."""
    main(["--log-dir", str(tmp_path), *FAKE_ARGUMENTS])

    lines = capsys.readouterr().err.splitlines()
    assert any(
        "ticket #11 · implement-ticket attempt 1 · fail" in line for line in lines
    )
    assert any(line.endswith("implement_spec · completed") for line in lines)


def test_a_worktree_that_cannot_be_closed_clean_fails_the_spec_not_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Given a run that raises WorktreeError outside the Workflow's error route, when the command handles it, then it logs the error and exits 1."""
    harness = ImplementSpecHarness(tmp_path)

    def worktree_error(*args: object, **kwargs: object) -> object:
        raise WorktreeError("worktree has retained edits")

    monkeypatch.setattr(entry, "run", worktree_error)

    code = harness.run()

    assert code == 1 and "retained edits" in capsys.readouterr().err


@pytest.mark.parametrize(
    "error",
    [
        LoopHookError(LoopHookPoint.WORKTREE_READY, "make setup", "failed"),
        CalledProcessError(128, ["git", "worktree", "add"]),
    ],
    ids=["a worktree hook that fails", "a worktree that cannot be added"],
)
def test_a_declared_error_opening_the_feature_worktree_fails_that_spec_with_a_comment_and_the_intake_goes_on(
    error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given two runnable Specs and a first run that raises a declared error outside any Stage, when the intake runs, then the first Spec gets the failure Comment, the second still runs and the command exits 1."""
    harness = ImplementSpecHarness(tmp_path)
    real_run, real_select = entry.run, entry.select_specs
    calls: list[int] = []

    def twice(*args: object, **kwargs: object) -> entry.Selection:
        selection = real_select(*args, **kwargs)  # type: ignore[arg-type]
        return entry.Selection(
            selection.runnable * 2, selection.unreadable, selection.awaiting_human
        )

    def first_fails(*args: object, **kwargs: object) -> object:
        calls.append(1)
        if len(calls) == 1:
            raise error
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(entry, "select_specs", twice)
    monkeypatch.setattr(entry, "run", first_fails)

    code = harness.run()

    assert code == 1 and len(calls) == 2
    assert sum("the Spec's run failed" in body for body in harness.rig.comments(1)) == 1
    assert harness.closed() == [10]


def test_an_unexpected_error_is_logged_and_the_command_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Given a run that raises an error nobody handles, when the command catches it last, then it exits 1 naming only the error type."""
    harness = ImplementSpecHarness(tmp_path)

    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr(entry, "run", boom)

    code = harness.run()

    logged = capsys.readouterr().err
    assert code == 1 and "RuntimeError" in logged and "boom" not in logged


def test_a_keyboard_interrupt_exits_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given an interrupt that reaches the command mid-run, when it is caught, then the command exits 130."""
    harness = ImplementSpecHarness(tmp_path)

    def interrupted(*args: object, **kwargs: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(entry, "run", interrupted)

    assert harness.run() == 130


def test_a_tracker_error_hands_the_spec_off_with_a_failure_comment(
    tmp_path: Path,
) -> None:
    """Given a tracker that refuses to close a Ticket, when the Spec runs, then the failure is commented on the Spec and the run exits 1."""
    harness = ImplementSpecHarness(tmp_path)
    harness.rig.gh.fail_on("issue", "close")

    code = harness.run()

    assert code == 1
    assert any("the Spec's run failed" in body for body in harness.rig.comments(1))
    assert "hitl" not in harness.labels(1)


def test_a_signal_asks_the_run_to_stop_and_gives_the_signals_back_to_their_default_action() -> (
    None
):
    """Given an interrupt request, when SIGINT arrives, then the run is asked to stop and a second SIGINT takes the default action."""
    saved = {name: signal.getsignal(name) for name in (signal.SIGINT, signal.SIGTERM)}
    request = entry.InterruptRequest()
    try:
        assert not request.requested()

        request.request(signal.SIGINT, None)

        assert request.requested()
        assert (
            signal.getsignal(signal.SIGINT) is signal.SIG_DFL
            and signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
        )
    finally:
        for name, handler in saved.items():
            signal.signal(name, handler)


def test_the_real_entry_point_installs_the_signal_handlers_only_while_it_runs(
    tmp_path: Path,
) -> None:
    """Given the command run with its own services, when it ends, then SIGINT and SIGTERM have the handlers they had before."""
    before = {name: signal.getsignal(name) for name in (signal.SIGINT, signal.SIGTERM)}

    main(["--log-dir", str(tmp_path), *FAKE_ARGUMENTS])

    assert {name: signal.getsignal(name) for name in before} == before


def test_default_harness_checkout_is_the_repository_root() -> None:
    """The relocated entry point still targets the harness repository root."""
    assert entry.harness_target().checkout == Path(__file__).resolve().parents[5]

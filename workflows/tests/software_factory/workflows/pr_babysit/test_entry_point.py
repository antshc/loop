"""The pr_babysit command line: options, argument checks, exit codes, the interrupt request and how it records what a worktree that could not be closed clean left behind."""

from __future__ import annotations

import json
import signal
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired

import pytest
from loop import DefinitionError, WorktreeError

from workflows.software_factory.infrastructure.telemetry import Tee
from workflows.software_factory.workflows.pr_babysit import __main__ as entry
from workflows.software_factory.workflows.pr_babysit.__main__ import StopRequest, main
from workflows.software_factory.workflows.pr_babysit.policy import (
    FINGERPRINT,
    REASON,
    fingerprint,
)
from workflows.software_factory.workflows.pr_babysit.settings import Settings

from .harness import Babysit
from .test_intake import Discovery
from .test_scenarios import addressing


def test_the_help_exits_0_and_lists_the_config_pr_include_existing_and_retry_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Given the --help option, when the command starts, then it exits 0 and lists every option."""
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    help_text = capsys.readouterr().out
    assert exit_info.value.code == 0
    assert all(
        option in help_text
        for option in ("--config", "--pr", "--include-existing", "--retry")
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ["--pr", "not-a-url"],
        [
            "--pr",
            "https://github.com/a/b/pull/1",
            "--retry",
            "https://github.com/a/b/pull/1",
        ],
        ["--pr", "https://github.com/a/b/pull/1", "--include-existing"],
    ],
    ids=[
        "a pull request that is no URL",
        "retry with an explicit pull request",
        "include-existing with an explicit pull request",
    ],
)
def test_arguments_that_do_not_fit_are_refused_with_a_usage_error(
    arguments: list[str], tmp_path: Path
) -> None:
    """Given a --pr that is no GitHub URL, or a discovery-only option beside it, when the command starts, then it exits with a usage error."""
    with pytest.raises(SystemExit) as exit_info:
        main(["--config", str(tmp_path / "babysit.json"), *arguments])

    assert exit_info.value.code == 2


def test_a_pull_request_of_an_unconfigured_repository_exits_1(tmp_path: Path) -> None:
    """Given a pull request whose repository is not configured, when the command runs, then it exits 1 before any Git operation."""
    rig = Babysit(tmp_path)

    code = main(
        ["--config", str(rig.config), "--pr", "https://github.com/c/d/pull/1"],
        services=rig.services,
    )

    assert code == 1 and rig.runner.calls == []


def test_a_missing_configuration_file_exits_1(tmp_path: Path) -> None:
    """Given a configuration file that does not exist, when the command runs, then it exits 1."""
    assert main(["--config", str(tmp_path / "missing.json")]) == 1


@pytest.mark.parametrize(
    ("extra", "expected"), [({}, "state/worktrees"), ({"worktrees_dir": ".wt"}, ".wt")]
)
def test_the_worktrees_live_under_the_state_folder_unless_the_configuration_names_a_folder(
    extra: dict[str, str], expected: str, tmp_path: Path
) -> None:
    """Given a configuration with or without `worktrees_dir`, when it loads, then the worktrees root is that folder relative to the file, else `worktrees` under the state folder."""
    config = tmp_path / "cfg.json"
    repository = {"name": "a/b", "path": ".", "build": ["build"], "test": ["test"]}
    config.write_text(
        json.dumps({"repositories": [repository], "state_dir": "state", **extra}),
        encoding="utf-8",
    )
    assert Settings.load(config).worktrees_root == (tmp_path / expected).resolve()


def test_a_worktree_that_cannot_be_closed_clean_is_recorded_as_a_hand_off_at_the_pull_requests_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a run that raises WorktreeError outside the Workflow's error route, when the command handles it, then the unclosed run is closed handed off with the reason and a fresh fingerprint and the command exits 1."""
    rig = addressing(tmp_path)

    def worktree_error(*args: object, **kwargs: object) -> object:
        rig.ledger.item("pr_babysit", rig.pr.id).append(
            {"kind": "run-opened", "epoch": 1}
        )
        raise WorktreeError("worktree has retained edits")

    monkeypatch.setattr(entry, "run", worktree_error)

    code = rig.babysit()

    status = rig.status()
    assert code == 1 and status is not None and status.status == "handed_off"
    assert status.attributes == {
        FINGERPRINT: fingerprint(rig.polled()),
        REASON: "worktree has retained edits",
    }


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            CalledProcessError(128, ["git", "fetch", "origin", "feature"]),
            "returned non-zero exit status 128",
        ),
        (TimeoutExpired(["git", "fetch"], 120), "timed out after 120 seconds"),
    ],
    ids=["a fetch that fails", "a fetch that times out"],
)
def test_an_error_the_workflow_declares_that_escapes_the_run_is_recorded_as_a_hand_off(
    error: Exception,
    reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given a run whose workspace opener raises a declared error outside any Stage guard, when the command handles it, then the run is closed handed off at the fingerprint and the command exits 1."""
    rig = addressing(tmp_path)

    def escaping(*args: object, **kwargs: object) -> object:
        rig.ledger.item("pr_babysit", rig.pr.id).append(
            {"kind": "run-opened", "epoch": 1}
        )
        raise error

    monkeypatch.setattr(entry, "run", escaping)

    code = rig.babysit()

    status = rig.status()
    assert code == 1 and status is not None and status.status == "handed_off"
    assert status.attributes[FINGERPRINT] == fingerprint(
        rig.polled()
    ) and reason in str(status.attributes[REASON])


def test_a_definition_error_is_not_recorded_as_a_hand_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a run refused before it opens because the definition is wrong, when the command handles it, then it exits 1 without closing a run."""
    rig = addressing(tmp_path)

    def refused(*args: object, **kwargs: object) -> object:
        raise DefinitionError("Services is missing GitHub")

    monkeypatch.setattr(entry, "run", refused)

    assert rig.babysit() == 1 and rig.status() is None


def test_an_interrupt_request_asks_the_run_to_stop_wakes_the_pacer_and_gives_the_signals_back() -> (
    None
):
    """Given an interrupt request, when SIGINT arrives, then the run is asked to stop, a waiting pacer returns at once and a second SIGINT takes the default action."""
    saved = {name: signal.getsignal(name) for name in (signal.SIGINT, signal.SIGTERM)}
    request = StopRequest()
    try:
        assert not request.requested()

        request.request(signal.SIGINT, None)
        request.wait(60)

        assert request.requested()
        assert (
            signal.getsignal(signal.SIGINT) is signal.SIG_DFL
            and signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
        )
    finally:
        for name, handler in saved.items():
            signal.signal(name, handler)


def test_the_signal_handlers_are_installed_only_while_the_command_runs() -> None:
    """Given the handlers the command installs, when the block ends, then SIGINT and SIGTERM have the handlers they had before."""
    before = {name: signal.getsignal(name) for name in (signal.SIGINT, signal.SIGTERM)}
    stop = StopRequest()

    with entry._handling(stop, True):
        assert signal.getsignal(signal.SIGINT) == stop.request

    assert {name: signal.getsignal(name) for name in before} == before


def test_telemetry_spans_are_exported_next_to_the_console_log_only_when_an_observer_is_configured(
    tmp_path: Path,
) -> None:
    """Given an observer, when the runtime is built, then spans go to the console log and the OpenTelemetry export; without one, to the console log alone."""
    settings = Settings((), tmp_path)

    with_observer = entry._runtime(settings, object(), StopRequest())  # type: ignore[arg-type]
    without = entry._runtime(settings, None, StopRequest())

    assert isinstance(with_observer.spans, Tee) and not isinstance(without.spans, Tee)


def test_a_discovery_that_is_asked_to_stop_exits_0_and_stops_its_workers(
    tmp_path: Path,
) -> None:
    """Given a stop signal that turns on after the first scan, when discovery ends, then it exits 0 and every worker it started was told to stop."""
    discovery = Discovery(tmp_path)

    code = discovery.serve(1, "--include-existing")

    assert code == 0 and len(discovery.children.started) == 1
    assert [child.returncode for child in discovery.children.children] == [0]


def test_the_real_services_are_exactly_what_the_definition_and_the_scheduler_need() -> (
    None
):
    """Given the real services, when they are built, then they are Git, the runtime's Git, GitHub, the agent launcher and the pacer."""
    names = sorted(
        type_.__name__ for type_ in entry.build_services(None, StopRequest()).types()
    )

    assert names == ["AgentLauncher", "GitClient", "GitHub", "Pacer", "WorktreeGit"]

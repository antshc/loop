"""The pr_babysit command line: options, argument checks, exit codes, the interrupt request and how it records what a worktree that could not be closed clean left behind."""

from __future__ import annotations

import json
import signal
from pathlib import Path
from queue import Queue
from subprocess import CalledProcessError, TimeoutExpired
from threading import Barrier, current_thread, main_thread
from unittest.mock import Mock

import pytest
from loop import (
    DefinitionError,
    FakeCliRunner,
    FakeReply,
    GitCli,
    GitRuntime,
    RunResult,
    WorktreeError,
    fake_agent,
)
from loop.shared.git import GitClient
from loop.shared.github.account import GitHub
from loop.shared.process import CommandRunner

from tests.shared.fakes import FakeGhCli
from workflows.software_factory.infrastructure.agents.launcher import AgentLauncher
from workflows.software_factory.infrastructure.pacing import Pacer
from workflows.software_factory.infrastructure.telemetry import Tee
from workflows.software_factory.workflows.pr_babysit import __main__ as entry
from workflows.software_factory.workflows.pr_babysit.__main__ import StopRequest, main
from workflows.software_factory.workflows.pr_babysit.policy import (
    FINGERPRINT,
    REASON,
    fingerprint,
)
from workflows.software_factory.workflows.pr_babysit.settings import Settings
from workflows.tests.software_factory.stages.pr_support import thread

from .harness import Babysit, LocalOrigin
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


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_an_interrupt_request_asks_the_run_to_stop_wakes_the_pacer_and_gives_the_signals_back(
    signum: int,
) -> None:
    """Given an interrupt request, when SIGINT arrives, then the run is asked to stop, a waiting pacer returns at once and a second SIGINT takes the default action."""
    saved = {name: signal.getsignal(name) for name in (signal.SIGINT, signal.SIGTERM)}
    request = StopRequest()
    try:
        assert not request.requested()

        request.request(signum, None)
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

    assert code == 0 and len(discovery.workers.started) == 1
    assert [child.returncode for child in discovery.workers.runs] == [0]


def test_the_real_services_are_exactly_what_the_definition_and_the_scheduler_need() -> (
    None
):
    """Given the real services, when they are built, then they are Git, the runtime's Git, GitHub, the agent launcher and the pacer."""
    names = sorted(
        type_.__name__ for type_ in entry.build_services(None, StopRequest()).types()
    )

    assert names == ["AgentLauncher", "GitClient", "GitHub", "Pacer", "WorktreeGit"]


def test_discovered_prs_in_one_repository_have_separate_services_worktrees_and_ledgers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two real workflow runs overlap in distinct worktrees of the same checkout."""
    rig = Babysit(tmp_path, threads=[thread("first", "Inspect cart")])
    second = rig.host.add(8, head_ref="feature", head_sha=rig.head)
    second.head_repo = "owner/repo"
    second.threads = [thread("second", "Inspect cart")]
    grouped = Barrier(2, timeout=10)
    completed: Queue[None] = Queue()
    paths, services_seen, ledgers = [], [], []
    original_build, original_run = entry.build_services, entry.run

    def answer(request, context):
        paths.append(context.agent.cwd)
        grouped.wait()
        # An invalid grouping hands off without edits or remote writes.
        return FakeReply("invalid grouping")

    def build(observer, stop):
        services = original_build(observer, stop)
        github = GitHub(FakeGhCli(pulls=rig.host))
        if current_thread() is main_thread():

            def wait(seconds):
                completed.get(timeout=15)
                completed.get(timeout=15)
                stop.stop()

            return services.replace(github, Pacer(wait))
        launcher = AgentLauncher(
            lambda: fake_agent(FakeCliRunner(answer, speed=0), GitRuntime(GitCli()))
        )
        services = services.replace(github, launcher).bind(
            GitClient, LocalOrigin(CommandRunner())
        )
        services_seen.append(services)
        return services

    def run(*args, **kwargs):
        ledgers.append(kwargs["ledger"])
        try:
            return original_run(*args, **kwargs)
        finally:
            completed.put(None)

    monkeypatch.setattr(entry, "build_services", build)
    monkeypatch.setattr(entry, "run", run)

    assert main(["--config", str(rig.config), "--include-existing"]) == 0
    assert {path.name for path in paths} == {"7", "8"}
    assert all(
        path.parent == tmp_path / "worktrees/babysit/owner/repo" for path in paths
    )
    assert len(services_seen) == 2 and services_seen[0] is not services_seen[1]
    assert len(ledgers) == 2 and ledgers[0] is not ledgers[1]
    for type_ in services_seen[0].types():
        assert services_seen[0].get(type_) is not services_seen[1].get(type_)
    assert rig.ledger.status("pr_babysit", "owner/repo#7").status == "handed_off"
    assert rig.ledger.status("pr_babysit", "owner/repo#8").status == "handed_off"
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize(
    "failure", [None, WorktreeError("retained edits"), RuntimeError("worker failed")]
)
def test_an_owned_observer_closes_after_success_or_worker_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure
) -> None:
    """Each PR invocation releases telemetry even when its workflow raises."""

    rig = Babysit(tmp_path)
    observer = Mock()
    monkeypatch.setattr(entry, "_observer", lambda _: observer)
    monkeypatch.setattr(entry, "build_services", lambda observer, stop: rig.services)

    def run(*args, **kwargs):
        if failure is not None:
            raise failure
        return RunResult("completed", None, 1)

    monkeypatch.setattr(entry, "run", run)
    argv = ["--config", str(rig.config), "--pr", "https://github.com/owner/repo/pull/7"]
    if type(failure) is RuntimeError:
        with pytest.raises(RuntimeError, match="worker failed"):
            main(argv)
    else:
        assert main(argv) == (1 if failure else 0)
    observer.close.assert_called_once()

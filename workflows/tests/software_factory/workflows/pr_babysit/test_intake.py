"""pr_babysit's discovery through the command line: which pull requests get a worker, read from the baseline and the Execution ledger, with fake GitHub and a fake child launcher."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from workflows.software_factory.events.pull_requests import PullRequestWork
from workflows.software_factory.events.specs import RepositoryTarget
from loop import Ledger, RecordingSpans, Services
from loop.shared.github.account import GitHub
from workflows.software_factory.infrastructure.pacing import Pacer
from workflows.software_factory.stages import pull_requests
from tests.shared.fakes import FakeGhCli, FakePullRequestHost, issue_comment
from workflows.tests.software_factory.workflows.pr_babysit.golden import GOLDEN, GREEN, OPEN, build_host
from workflows.software_factory.workflows.pr_babysit.definition import NAME
from workflows.software_factory.workflows.pr_babysit.__main__ import Runtime, main
from workflows.software_factory.workflows.pr_babysit.intake import ChildLauncher, Registry, child_environment
from workflows.software_factory.workflows.pr_babysit.policy import FINGERPRINT, Blocked, fingerprint


class Child:
    """A worker process that has not exited until it is told to."""

    returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


class Children:
    """The launcher: remembers every pull request it started a worker for."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.children: list[Child] = []

    def start(self, config: Path, url: str) -> Child:
        self.started.append(url)
        self.children.append(Child())
        return self.children[-1]


class Stopping:
    """The stop signal, on after `ticks` waits."""

    def __init__(self) -> None:
        self.on = False

    def requested(self) -> bool:
        return self.on


class Discovery:
    """Open pull requests of a/b authored by the operator, a ledger, a child launcher and a pacer that ends discovery after `ticks` waits."""

    def __init__(self, tmp_path: Path, host: FakePullRequestHost | None = None, *, concurrency: int = 2) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp_path = tmp_path
        self.host = host or FakePullRequestHost()
        if host is None:
            self.host.add(1, head_sha="sha1")
        self.github = GitHub(FakeGhCli(pulls=self.host))
        self.ledger = Ledger(tmp_path / "ledger")
        self.children = Children()
        self.stop = Stopping()
        self.config = tmp_path / "babysit.json"
        self.config.write_text(json.dumps({
            "repositories": [{"name": "a/b", "path": str(tmp_path), "build": ["build"], "test": ["test"]}],
            "state_dir": str(tmp_path), "interval": 0.01, "concurrency": concurrency,
        }))
        self.waits = 0

    def serve(self, ticks: int, *argv: str, on_tick=lambda tick: None) -> int:
        """Runs discovery for `ticks` scans, calling `on_tick` after each."""
        self.waits = 0

        def wait(seconds: float) -> None:
            self.waits += 1
            on_tick(self.waits)
            self.stop.on = self.waits >= ticks

        self.stop.on = False
        services = Services(self.github, Pacer(wait), Runtime(self.ledger, RecordingSpans(), self.stop)).bind(ChildLauncher, self.children)
        return main(["--config", str(self.config), *argv], services=services)

    def work(self, number: int) -> PullRequestWork:
        return PullRequestWork(RepositoryTarget("a/b", self.tmp_path, self.tmp_path / "worktrees"), number, "pull-request")

    def block(self, number: int, blocked_at: str, **attributes) -> None:
        """Records a run of the pull request that ended handed off at the fingerprint."""
        log = self.ledger.item(NAME, f"a/b#{number}")
        log.seed([
            {"kind": "run-opened", "epoch": 1},
            {"kind": "run-closed", "status": "handed_off", "reason": "stuck", "attributes": {FINGERPRINT: blocked_at, **attributes}},
        ])

    def current(self, number: int) -> str:
        return fingerprint(pull_requests.read(self.work(number), self.github))

    def registry(self) -> dict:
        return json.loads((self.tmp_path / "scheduler.json").read_text())


@pytest.fixture
def discovery(tmp_path: Path) -> Discovery:
    return Discovery(tmp_path)


def test_the_first_baseline_is_ignored_and_a_pull_request_that_appears_later_is_started(discovery: Discovery) -> None:
    """Given pull requests on the first scan, when a new one appears on a later scan, then only the new one gets a worker."""
    def appear(tick: int) -> None:
        if tick == 1:
            discovery.host.add(2, head_sha="sha2")

    discovery.serve(3, on_tick=appear)

    assert discovery.children.started == ["https://github.com/a/b/pull/2"]
    assert [record["ignored"] for record in discovery.registry()["prs"].values()] == [True, False]


def test_existing_pull_requests_are_enrolled_with_the_include_existing_option_and_each_is_started_once(discovery: Discovery) -> None:
    """Given include-existing, when discovery runs several scans, then each open pull request gets exactly one worker while it runs."""
    discovery.host.add(2, head_sha="sha2")

    discovery.serve(3, "--include-existing")

    assert discovery.children.started == ["https://github.com/a/b/pull/1", "https://github.com/a/b/pull/2"]


def test_a_blocked_pull_request_is_started_again_only_after_its_fingerprint_changes_and_is_released_first(discovery: Discovery) -> None:
    """Given a pull request blocked at its current fingerprint, when its head changes, then a worker starts and the ledger records a release."""
    discovery.serve(1, "--include-existing")
    discovery.children.started.clear()
    discovery.block(1, discovery.current(1))

    discovery.serve(2)
    unchanged = list(discovery.children.started)
    discovery.host.pulls[1].head_sha = "new"
    discovery.serve(2)

    assert unchanged == [] and discovery.children.started == ["https://github.com/a/b/pull/1"]
    assert discovery.ledger.status(NAME, "a/b#1").released


def test_an_unchanged_blocked_pull_request_gets_no_worker_so_no_agent_runs_and_nothing_is_pushed(discovery: Discovery) -> None:
    """Given a blocked pull request without a retry and with an unchanged fingerprint, when discovery runs, then it is not enrolled again for a worker and nothing is released."""
    discovery.serve(1, "--include-existing")
    discovery.children.started.clear()
    discovery.block(1, discovery.current(1))

    discovery.serve(3)

    assert discovery.children.started == [] and not discovery.ledger.status(NAME, "a/b#1").released


def test_a_blocked_pull_request_with_new_feedback_gets_a_worker(tmp_path: Path) -> None:
    """Given the golden pull request blocked at the fingerprint computed before the migration, when new feedback arrives, then it is started and the unchanged one is not."""
    host = build_host()
    host.pulls.pop(GREEN)
    discovery = Discovery(tmp_path, host)
    discovery.serve(1, "--include-existing")
    discovery.children.started.clear()
    discovery.block(OPEN, json.loads(GOLDEN.read_text())["open"]["fingerprint"])

    discovery.serve(2)
    unchanged = list(discovery.children.started)
    host.pulls[OPEN].comments.append(issue_comment(900, "reviewer", "One more thing"))
    discovery.serve(2)

    assert unchanged == [] and discovery.children.started == [f"https://github.com/a/b/pull/{OPEN}"]


def test_a_retry_releases_a_blocked_pull_request_and_starts_its_worker(discovery: Discovery) -> None:
    """Given a blocked pull request at an unchanged fingerprint, when discovery is asked to retry its URL, then the ledger records a release, a worker starts, and another blocked pull request stays held."""
    discovery.host.add(2, head_sha="sha2")
    discovery.serve(1, "--include-existing")
    discovery.children.started.clear()
    for number in (1, 2):
        discovery.block(number, discovery.current(number))

    discovery.serve(2, "--retry", "https://github.com/a/b/pull/1")

    assert discovery.children.started == ["https://github.com/a/b/pull/1"]
    assert discovery.ledger.status(NAME, "a/b#1").released and not discovery.ledger.status(NAME, "a/b#2").released


def test_a_worker_that_died_without_closing_its_run_is_not_started_again_without_an_explicit_retry(discovery: Discovery) -> None:
    """Given a worker that exits while its run never closed, when discovery goes on, then the pull request is held for an explicit retry, which starts it again."""
    def die(tick: int) -> None:
        for child in discovery.children.children:
            child.returncode = 1

    discovery.serve(4, "--include-existing", on_tick=die)
    after_crash = list(discovery.children.started)
    discovery.serve(2)
    held = list(discovery.children.started)
    discovery.serve(2, "--retry", "https://github.com/a/b/pull/1")

    assert after_crash == ["https://github.com/a/b/pull/1"] and held == after_crash
    assert discovery.children.started == after_crash * 2
    assert discovery.registry()["prs"]["a/b#1"]["crashed"] is False


def corrupt(discovery: Discovery, number: int) -> Path:
    """Damages a line of the pull request's log that is not the last one."""
    log = discovery.ledger.item(NAME, f"a/b#{number}")
    log.seed([{"kind": "run-opened", "epoch": 1}, {"kind": "run-closed", "status": "completed", "reason": None, "attributes": {}}])
    lines = log.path.read_text().splitlines(keepends=True)
    log.path.write_text("{damaged\n" + "".join(lines[1:]))
    return log.path


def test_a_damaged_log_marks_only_its_pull_request_crashed_and_the_others_still_start(
    discovery: Discovery, caplog: pytest.LogCaptureFixture,
) -> None:
    """Given two pull requests and a damaged log for the first, when discovery scans, then the second still gets a worker, the first is marked crashed and the log names the file."""
    discovery.host.add(2, head_sha="sha2")
    path = corrupt(discovery, 1)

    code = discovery.serve(2, "--include-existing")

    assert code == 0 and discovery.children.started == ["https://github.com/a/b/pull/2"]
    assert discovery.registry()["prs"]["a/b#1"]["crashed"] is True
    assert any("a/b#1" in record.message and str(path) in record.message for record in caplog.records)


def test_a_damaged_log_of_an_exited_worker_does_not_stop_the_scheduler(discovery: Discovery) -> None:
    """Given a worker whose log is damaged when it exits, when discovery reaps it, then the scheduler goes on and marks the pull request crashed."""
    def die_damaged(tick: int) -> None:
        if tick == 1:
            corrupt(discovery, 1)
            for child in discovery.children.children:
                child.returncode = 1

    code = discovery.serve(3, "--include-existing", on_tick=die_damaged)

    assert code == 0 and discovery.children.started == ["https://github.com/a/b/pull/1"]
    assert discovery.registry()["prs"]["a/b#1"]["crashed"] is True


def test_a_reopened_pull_request_whose_run_completed_starts_again(discovery: Discovery) -> None:
    """Given a pull request whose run completed, when it is open again, then discovery starts a worker for it."""
    discovery.serve(1, "--include-existing")
    discovery.children.started.clear()
    log = discovery.ledger.item(NAME, "a/b#1")
    log.seed([{"kind": "run-opened", "epoch": 1}, {"kind": "run-closed", "status": "completed", "reason": None, "attributes": {}}])

    discovery.serve(1)

    assert discovery.children.started == ["https://github.com/a/b/pull/1"]


def test_the_concurrency_limit_holds_back_the_other_pull_requests(tmp_path: Path) -> None:
    """Given a limit of one worker, when two pull requests are open, then only one worker starts."""
    discovery = Discovery(tmp_path, concurrency=1)
    discovery.host.add(2, head_sha="sha2")

    discovery.serve(2, "--include-existing")

    assert len(discovery.children.started) == 1


def test_a_pull_request_whose_work_item_a_surviving_worker_still_holds_is_not_started(discovery: Discovery) -> None:
    """Given a worker of an interrupted scheduler that still holds the pull request's work item, when discovery runs, then no second worker starts."""
    with discovery.ledger.item(NAME, "a/b#1").lock():
        discovery.serve(2, "--include-existing")

    assert discovery.children.started == []


def test_trace_context_is_not_inherited_by_a_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Given distributed trace context in the environment, when a worker's environment is built, then it is dropped and the exporter configuration kept."""
    monkeypatch.setenv("TRACEPARENT", "parent")
    monkeypatch.setenv("baggage", "something")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    environment = child_environment()

    assert "TRACEPARENT" not in environment and "baggage" not in environment
    assert environment["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://localhost:4318"


def test_a_change_of_discovery_configuration_does_not_silently_rebaseline(tmp_path: Path) -> None:
    """Given a baseline for one author, when another author opens it, then it refuses."""
    Registry(tmp_path, "me", ["a/b"]).enroll([], False)

    with pytest.raises(Blocked, match="configuration changed"):
        Registry(tmp_path, "another-user", ["a/b"])

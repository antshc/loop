"""pr_babysit's discovery through the command line: which pull requests get a worker, read from the baseline and the Execution ledger, with fake GitHub and synchronized thread workers."""

from __future__ import annotations

import json
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from threading import Event
from unittest.mock import patch

import pytest
from loop import Ledger, RecordingSpans, Services
from loop.shared.github.account import GitHub

from tests.shared.fakes import FakeGhCli, FakePullRequestHost, issue_comment
from workflows.software_factory.events.pull_requests import PullRequestWork
from workflows.software_factory.events.specs import RepositoryTarget
from workflows.software_factory.infrastructure.pacing import Pacer
from workflows.software_factory.infrastructure.state import StateError, process_lock
from workflows.software_factory.stages import pull_requests
from workflows.software_factory.workflows.pr_babysit.__main__ import (
    Runtime,
    StopRequest,
    _discover,
    main,
)
from workflows.software_factory.workflows.pr_babysit.definition import NAME
from workflows.software_factory.workflows.pr_babysit.intake import (
    DiscoveryRequest,
    Maintain,
    Registry,
)
from workflows.software_factory.workflows.pr_babysit.policy import (
    FINGERPRINT,
    Blocked,
    fingerprint,
)
from workflows.software_factory.workflows.pr_babysit.settings import Settings
from workflows.tests.software_factory.workflows.pr_babysit.golden import (
    GOLDEN,
    GREEN,
    OPEN,
    build_host,
)


class Worker:
    """A PR action released explicitly by the scheduler's test pacer."""

    def __init__(self) -> None:
        self.finished = Event()
        self._returncode: int | None = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    @returncode.setter
    def returncode(self, value: int) -> None:
        self._returncode = value
        self.finished.set()


class Workers:
    """Records PR invocations and holds them until the test releases their action."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.started: list[str] = []
        self.runs: list[Worker] = []
        self.entered: Queue[None] = Queue()

    def __call__(self, slug: str, number: int) -> int:
        worker = Worker()
        self.started.append(f"https://github.com/{slug}/pull/{number}")
        self.runs.append(worker)
        self.entered.put(None)
        assert worker.finished.wait(5), "worker was not released"
        if worker.returncode:
            raise RuntimeError("unexpected worker failure")
        if self.ledger.status(NAME, f"{slug}#{number}") is None:
            self.ledger.item(NAME, f"{slug}#{number}").seed(
                [
                    {"kind": "run-opened", "epoch": 1},
                    {
                        "kind": "run-closed",
                        "status": "interrupted",
                        "reason": None,
                        "attributes": {},
                    },
                ]
            )
        return 0


class Stopping:
    """The scheduler stop signal controlled by the test pacer."""

    def __init__(self) -> None:
        self.on = False

    def requested(self) -> bool:
        return self.on


class Discovery:
    """Open pull requests of a/b authored by the operator, a ledger, thread workers and a pacer that ends discovery after `ticks` waits."""

    def __init__(
        self,
        tmp_path: Path,
        host: FakePullRequestHost | None = None,
        *,
        concurrency: int = 2,
    ) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp_path = tmp_path
        self.host = host or FakePullRequestHost()
        if host is None:
            self.host.add(1, head_sha="sha1")
        self.github = GitHub(FakeGhCli(pulls=self.host))
        self.ledger = Ledger(tmp_path / "ledger")
        self.workers = Workers(self.ledger)
        self.stop = Stopping()
        self.config = tmp_path / "babysit.json"
        self.config.write_text(
            json.dumps(
                {
                    "repositories": [
                        {
                            "name": "a/b",
                            "path": str(tmp_path),
                            "build": ["build"],
                            "test": ["test"],
                        }
                    ],
                    "state_dir": str(tmp_path),
                    "interval": 0.01,
                    "concurrency": concurrency,
                }
            )
        )
        self.waits = 0

    def serve(self, ticks: int, *argv: str, on_tick=lambda tick: None) -> int:
        """Runs discovery for `ticks` scans, calling `on_tick` after each."""
        self.waits = 0

        futures: list[Future[int]] = []
        workers = self.workers

        class StartedExecutor(ThreadPoolExecutor):
            def submit(self, fn, /, *args, **kwargs):
                future = super().submit(fn, *args, **kwargs)
                futures.append(future)
                workers.entered.get(timeout=5)
                return future

        def wait(seconds: float) -> None:
            self.waits += 1
            on_tick(self.waits)
            self.stop.on = self.waits >= ticks
            if self.stop.on:
                for worker in workers.runs:
                    if worker.returncode is None:
                        worker.returncode = 0
            # Let explicitly completed actions finish before the next scan.
            for worker, future in zip(workers.runs[-len(futures) :], futures):
                if worker.finished.is_set():
                    try:
                        future.result(timeout=5)
                    except RuntimeError:
                        pass

        self.stop.on = False
        services = Services(
            self.github, Pacer(wait), Runtime(self.ledger, RecordingSpans(), self.stop)
        ).bind(Maintain, self.workers)
        with patch(
            "workflows.software_factory.workflows.pr_babysit.intake.ThreadPoolExecutor",
            StartedExecutor,
        ):
            return main(["--config", str(self.config), *argv], services=services)

    def work(self, number: int) -> PullRequestWork:
        return PullRequestWork(
            RepositoryTarget("a/b", self.tmp_path, self.tmp_path / "worktrees"),
            number,
            "pull-request",
        )

    def block(self, number: int, blocked_at: str, **attributes) -> None:
        """Records a run of the pull request that ended handed off at the fingerprint."""
        log = self.ledger.item(NAME, f"a/b#{number}")
        log.seed(
            [
                {"kind": "run-opened", "epoch": 1},
                {
                    "kind": "run-closed",
                    "status": "handed_off",
                    "reason": "stuck",
                    "attributes": {FINGERPRINT: blocked_at, **attributes},
                },
            ]
        )

    def current(self, number: int) -> str:
        return fingerprint(pull_requests.read(self.work(number), self.github))

    def registry(self) -> dict:
        return json.loads((self.tmp_path / "scheduler.json").read_text())


@pytest.fixture
def discovery(tmp_path: Path) -> Discovery:
    return Discovery(tmp_path)


def test_the_first_baseline_is_ignored_and_a_pull_request_that_appears_later_is_started(
    discovery: Discovery,
) -> None:
    """Given pull requests on the first scan, when a new one appears on a later scan, then only the new one gets a worker."""

    def appear(tick: int) -> None:
        if tick == 1:
            discovery.host.add(2, head_sha="sha2")

    discovery.serve(3, on_tick=appear)

    assert discovery.workers.started == ["https://github.com/a/b/pull/2"]
    assert [record["ignored"] for record in discovery.registry()["prs"].values()] == [
        True,
        False,
    ]


def test_existing_pull_requests_are_enrolled_with_the_include_existing_option_and_each_is_started_once(
    discovery: Discovery,
) -> None:
    """Given include-existing, when discovery runs several scans, then each open pull request gets exactly one worker while it runs."""
    discovery.host.add(2, head_sha="sha2")

    discovery.serve(3, "--include-existing")

    assert discovery.workers.started == [
        "https://github.com/a/b/pull/1",
        "https://github.com/a/b/pull/2",
    ]


def test_a_blocked_pull_request_is_started_again_only_after_its_fingerprint_changes_and_is_released_first(
    discovery: Discovery,
) -> None:
    """Given a pull request blocked at its current fingerprint, when its head changes, then a worker starts and the ledger records a release."""
    discovery.serve(1, "--include-existing")
    discovery.workers.started.clear()
    discovery.block(1, discovery.current(1))

    discovery.serve(2)
    unchanged = list(discovery.workers.started)
    discovery.host.pulls[1].head_sha = "new"
    discovery.serve(2)

    assert unchanged == [] and discovery.workers.started == [
        "https://github.com/a/b/pull/1"
    ]
    assert discovery.ledger.status(NAME, "a/b#1").released


def test_an_unchanged_blocked_pull_request_gets_no_worker_so_no_agent_runs_and_nothing_is_pushed(
    discovery: Discovery,
) -> None:
    """Given a blocked pull request without a retry and with an unchanged fingerprint, when discovery runs, then it is not enrolled again for a worker and nothing is released."""
    discovery.serve(1, "--include-existing")
    discovery.workers.started.clear()
    discovery.block(1, discovery.current(1))

    discovery.serve(3)

    assert (
        discovery.workers.started == []
        and not discovery.ledger.status(NAME, "a/b#1").released
    )


def test_a_blocked_pull_request_with_new_feedback_gets_a_worker(tmp_path: Path) -> None:
    """Given the golden pull request blocked at the fingerprint computed before the migration, when new feedback arrives, then it is started and the unchanged one is not."""
    host = build_host()
    host.pulls.pop(GREEN)
    discovery = Discovery(tmp_path, host)
    discovery.serve(1, "--include-existing")
    discovery.workers.started.clear()
    discovery.block(OPEN, json.loads(GOLDEN.read_text())["open"]["fingerprint"])

    discovery.serve(2)
    unchanged = list(discovery.workers.started)
    host.pulls[OPEN].comments.append(issue_comment(900, "reviewer", "One more thing"))
    discovery.serve(2)

    assert unchanged == [] and discovery.workers.started == [
        f"https://github.com/a/b/pull/{OPEN}"
    ]


def test_a_retry_releases_a_blocked_pull_request_and_starts_its_worker(
    discovery: Discovery,
) -> None:
    """Given a blocked pull request at an unchanged fingerprint, when discovery is asked to retry its URL, then the ledger records a release, a worker starts, and another blocked pull request stays held."""
    discovery.host.add(2, head_sha="sha2")
    discovery.serve(1, "--include-existing")
    discovery.workers.started.clear()
    for number in (1, 2):
        discovery.block(number, discovery.current(number))

    discovery.serve(2, "--retry", "https://github.com/a/b/pull/1")

    assert discovery.workers.started == ["https://github.com/a/b/pull/1"]
    assert (
        discovery.ledger.status(NAME, "a/b#1").released
        and not discovery.ledger.status(NAME, "a/b#2").released
    )


def test_a_worker_that_died_without_closing_its_run_is_not_started_again_without_an_explicit_retry(
    discovery: Discovery,
) -> None:
    """Given a worker that exits while its run never closed, when discovery goes on, then the pull request is held for an explicit retry, which starts it again."""

    def die(tick: int) -> None:
        for child in discovery.workers.runs:
            child.returncode = 1

    discovery.serve(4, "--include-existing", on_tick=die)
    after_crash = list(discovery.workers.started)
    discovery.serve(2)
    held = list(discovery.workers.started)
    discovery.serve(2, "--retry", "https://github.com/a/b/pull/1")

    assert after_crash == ["https://github.com/a/b/pull/1"] and held == after_crash
    assert discovery.workers.started == after_crash * 2
    assert discovery.registry()["prs"]["a/b#1"]["crashed"] is False


def corrupt(discovery: Discovery, number: int) -> Path:
    """Damages a line of the pull request's log that is not the last one."""
    log = discovery.ledger.item(NAME, f"a/b#{number}")
    log.seed(
        [
            {"kind": "run-opened", "epoch": 1},
            {
                "kind": "run-closed",
                "status": "completed",
                "reason": None,
                "attributes": {},
            },
        ]
    )
    lines = log.path.read_text().splitlines(keepends=True)
    log.path.write_text("{damaged\n" + "".join(lines[1:]))
    return log.path


def test_a_damaged_log_marks_only_its_pull_request_crashed_and_the_others_still_start(
    discovery: Discovery,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Given two pull requests and a damaged log for the first, when discovery scans, then the second still gets a worker, the first is marked crashed and the log names the file."""
    discovery.host.add(2, head_sha="sha2")
    path = corrupt(discovery, 1)

    code = discovery.serve(2, "--include-existing")

    assert code == 0 and discovery.workers.started == ["https://github.com/a/b/pull/2"]
    assert discovery.registry()["prs"]["a/b#1"]["crashed"] is True
    assert any(
        "a/b#1" in record.message and str(path) in record.message
        for record in caplog.records
    )


def test_a_damaged_log_of_an_exited_worker_does_not_stop_the_scheduler(
    discovery: Discovery,
) -> None:
    """Given a worker whose log is damaged when it exits, when discovery reaps it, then the scheduler goes on and marks the pull request crashed."""

    def die_damaged(tick: int) -> None:
        if tick == 1:
            corrupt(discovery, 1)
            for child in discovery.workers.runs:
                child.returncode = 1

    code = discovery.serve(3, "--include-existing", on_tick=die_damaged)

    assert code == 0 and discovery.workers.started == ["https://github.com/a/b/pull/1"]
    assert discovery.registry()["prs"]["a/b#1"]["crashed"] is True


def test_a_reopened_pull_request_whose_run_completed_starts_again(
    discovery: Discovery,
) -> None:
    """Given a pull request whose run completed, when it is open again, then discovery starts a worker for it."""
    discovery.serve(1, "--include-existing")
    discovery.workers.started.clear()
    log = discovery.ledger.item(NAME, "a/b#1")
    log.seed(
        [
            {"kind": "run-opened", "epoch": 1},
            {
                "kind": "run-closed",
                "status": "completed",
                "reason": None,
                "attributes": {},
            },
        ]
    )

    discovery.serve(1)

    assert discovery.workers.started == ["https://github.com/a/b/pull/1"]


def test_the_concurrency_limit_holds_back_the_other_pull_requests(
    tmp_path: Path,
) -> None:
    """Given a limit of one worker, when two pull requests are open, then only one worker starts."""
    discovery = Discovery(tmp_path, concurrency=1)
    discovery.host.add(2, head_sha="sha2")

    discovery.serve(2, "--include-existing")

    assert len(discovery.workers.started) == 1


def test_a_pull_request_whose_work_item_a_surviving_worker_still_holds_is_not_started(
    discovery: Discovery,
) -> None:
    """Given a worker of an interrupted scheduler that still holds the pull request's work item, when discovery runs, then no second worker starts."""
    with discovery.ledger.item(NAME, "a/b#1").lock():
        discovery.serve(2, "--include-existing")

    assert discovery.workers.started == []


def test_a_change_of_discovery_configuration_does_not_silently_rebaseline(
    tmp_path: Path,
) -> None:
    """Given a baseline for one author, when another author opens it, then it refuses."""
    Registry(tmp_path, "me", ["a/b"]).enroll([], False)

    with pytest.raises(Blocked, match="configuration changed"):
        Registry(tmp_path, "another-user", ["a/b"])


def test_a_third_pr_starts_only_after_one_of_two_active_workers_finishes(
    discovery: Discovery,
) -> None:
    """A completed action frees one slot while the other PR keeps running."""
    discovery.host.add(2, head_sha="sha2")
    discovery.host.add(3, head_sha="sha3")
    active_counts = []

    def complete_first(tick: int) -> None:
        active_counts.append(
            sum(worker.returncode is None for worker in discovery.workers.runs)
        )
        if tick == 1:
            assert len(discovery.workers.started) == 2
            discovery.host.pulls[1].close()
            discovery.workers.runs[0].returncode = 0
        elif tick == 2:
            assert discovery.workers.started[-1].endswith("/3")
            assert discovery.workers.runs[1].returncode is None

    discovery.serve(3, "--include-existing", on_tick=complete_first)

    assert discovery.workers.started == [
        f"https://github.com/a/b/pull/{number}" for number in (1, 2, 3)
    ]
    assert max(active_counts) == 2


def test_a_worker_exception_frees_its_slot_without_stopping_another_pr(
    discovery: Discovery, caplog: pytest.LogCaptureFixture
) -> None:
    """One unexpected exception is quarantined while another PR keeps its thread."""
    discovery.host.add(2, head_sha="sha2")
    discovery.host.add(3, head_sha="sha3")

    def fail_first(tick: int) -> None:
        if tick == 1:
            discovery.workers.runs[0].returncode = 1
        if tick == 2:
            assert len(discovery.workers.started) == 3
            assert discovery.workers.runs[1].returncode is None

    discovery.serve(3, "--include-existing", on_tick=fail_first)

    assert discovery.registry()["prs"]["a/b#1"]["crashed"] is True
    assert "a/b#1: worker failed" in caplog.text


def test_a_scheduler_error_notifies_workers_and_keeps_its_lock_until_they_exit(
    discovery: Discovery,
) -> None:
    """Error cleanup wakes workers before joining and keeps their ownership intact."""
    stop = StopRequest()
    entered, finished = Event(), Event()

    def maintain(slug: str, number: int) -> int:
        entered.set()
        stop.wait(5)
        assert stop.requested()
        with pytest.raises(StateError):
            with process_lock(discovery.tmp_path / "scheduler.lock"):
                pass
        log = discovery.ledger.item(NAME, f"{slug}#{number}")
        log.seed(
            [
                {"kind": "run-opened", "epoch": 1},
                {"kind": "run-closed", "status": "interrupted", "attributes": {}},
            ]
        )
        finished.set()
        return 130

    def fail(seconds: float) -> None:
        assert entered.wait(5)
        raise RuntimeError("scheduler failed")

    services = Services(discovery.github, Pacer(fail)).bind(Maintain, maintain)
    with pytest.raises(RuntimeError, match="scheduler failed"):
        _discover(
            Settings.load(discovery.config),
            services,
            DiscoveryRequest(include_existing=True),
            stop,
        )

    assert finished.is_set() and stop.requested()
    assert not discovery.registry()["prs"]["a/b#1"].get("crashed", False)
    with process_lock(discovery.tmp_path / "scheduler.lock"):
        pass

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from software_factory.legacy_slices.pr_babysit.composition.entry import build_container
from software_factory.legacy_slices.pr_babysit.discover.children import ChildLauncher, child_environment
from software_factory.legacy_slices.pr_babysit.discover.registry import Registry
from software_factory.legacy_slices.pr_babysit.discover.scheduler import Scheduler
from software_factory.legacy_slices.pr_babysit.model import Blocked, DiscoveryRequest, Pacer, PrState, StopSignal
from software_factory.legacy_slices.pr_babysit.settings import Repository, Settings
from software_factory.legacy_slices.pr_babysit.watch.feedback import fingerprint
from software_factory.legacy_slices.pr_babysit.watch.snapshot import babysit_snapshot
from software_factory.shared.github.account import GitHub
from software_factory.shared.telemetry import ObserverFactory

from software_factory.tests.legacy_slices.pr_babysit.support import Pace, bind
from software_factory.tests.legacy_slices.pr_babysit.watch.golden import GOLDEN, GREEN, OPEN, build_host
from software_factory.tests.shared.fakes import FakeGhCli, FakePullRequestHost, issue_comment


def pr(number):
    host = FakePullRequestHost()
    host.add(number)
    return GitHub(FakeGhCli(pulls=host)).pull_requests("a/b").pull_request(number)


class Github:
    """Open pull requests of a/b authored by the operator, served by the shared `gh` fake."""

    def __init__(self, host=None):
        self.host = host or FakePullRequestHost()
        if host is None:
            self.host.add(1, head_sha="sha1")
        self.account = GitHub(FakeGhCli(pulls=self.host))

    def add(self, number):
        self.host.add(number, head_sha=f"sha{number}")

    def change_head(self, sha):
        self.host.pulls[1].head_sha = sha

    def snapshot(self, number):
        return babysit_snapshot(self.account.pull_requests("a/b").snapshot(number))


class Child:
    returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class Children:
    def __init__(self):
        self.started = []

    def start(self, config, url):
        self.started.append(url)
        return Child()


def settings(tmp_path):
    return Settings((Repository("a/b", tmp_path, ("build",), ("test",)),), tmp_path / "state")


def serve(config, tmp_path, github, children, on_wait, stop, *extra, include_existing=False):
    container = build_container(
        config, bind((GitHub, github.account), (ChildLauncher, children), (Pacer, Pace(on_wait)), (StopSignal, stop), *extra),
        discovery=DiscoveryRequest(tmp_path / "config.json", include_existing))
    try:
        return container.get(Scheduler).serve()
    finally:
        container.close()


def stop_after(ticks, on_tick=lambda tick: None):
    """A pacer callback that runs `on_tick`, then stops the scheduler after `ticks` waits."""
    stop, count = StopSignal(), 0
    def wait(seconds):
        nonlocal count
        count += 1
        on_tick(count)
        if count == ticks:
            stop.request()
    return wait, stop


def test_first_baseline_then_new_pr_only(tmp_path):
    registry = Registry(tmp_path, "me", ["a/b"])
    registry.enroll([("a/b", pr(1))], False)
    assert PrState(tmp_path, "a/b", 1).data["status"] == "ignored"
    registry = Registry(tmp_path, "me", ["a/b"])
    registry.enroll([("a/b", pr(1)), ("a/b", pr(2))], False)
    assert PrState(tmp_path, "a/b", 2).data["status"] == "queued"
    assert len(registry.data["prs"]) == 2


def test_scheduler_deduplicates_and_starts_new_pr_while_another_runs(tmp_path):
    config, github, children = replace(settings(tmp_path), otel_endpoint="http://localhost:4318"), Github(), Children()
    def refuse(endpoint):
        raise AssertionError("scheduler initialized telemetry")
    def discover_second(tick):
        github.add(2)
    wait, stop = stop_after(3, discover_second)
    serve(config, tmp_path, github, children, wait, stop, (ObserverFactory, refuse), include_existing=True)
    assert len(children.started) == 2
    assert children.started[0].endswith("/1")
    assert children.started[1].endswith("/2")


def test_blocked_pr_resumes_only_after_remote_change(tmp_path):
    config, github, children = settings(tmp_path), Github(), Children()
    registry = Registry(config.state_dir, "me", ["a/b"])
    registry.enroll([("a/b", pr(1))], True)
    state = PrState(config.state_dir, "a/b", 1)
    state.status("blocked", fingerprint=fingerprint(github.snapshot(1)))
    def change_head(tick):
        github.change_head("new")
    wait, stop = stop_after(2, change_head)
    serve(config, tmp_path, github, children, wait, stop)
    assert len(children.started) == 1


def _blocked_before_the_migration(tmp_path):
    """Babysit state of the representative open PR, blocked with the fingerprint the pre-migration code computed."""
    host = build_host()
    host.pulls.pop(GREEN)
    config = settings(tmp_path)
    Registry(config.state_dir, "me", ["a/b"]).enroll([("a/b", pr(OPEN))], True)
    PrState(config.state_dir, "a/b", OPEN).status("blocked", fingerprint=json.loads(GOLDEN.read_text())["open"]["fingerprint"])
    return config, Github(host)


def test_an_unchanged_blocked_pr_is_not_relaunched_after_the_migration(tmp_path):
    config, github = _blocked_before_the_migration(tmp_path)
    children = Children()
    wait, stop = stop_after(2)
    serve(config, tmp_path, github, children, wait, stop)
    assert children.started == []


def test_a_blocked_pr_with_new_feedback_is_relaunched(tmp_path):
    config, github = _blocked_before_the_migration(tmp_path)
    github.host.pulls[OPEN].comments.append(issue_comment(900, "reviewer", "One more thing"))
    children = Children()
    wait, stop = stop_after(2)
    serve(config, tmp_path, github, children, wait, stop)
    assert len(children.started) == 1


def test_reopened_enrolled_pr_starts_again(tmp_path):
    config, github, children = settings(tmp_path), Github(), Children()
    registry = Registry(config.state_dir, "me", ["a/b"])
    registry.enroll([("a/b", pr(1))], True)
    PrState(config.state_dir, "a/b", 1).status("completed")
    wait, stop = stop_after(1)
    serve(config, tmp_path, github, children, wait, stop)
    assert len(children.started) == 1


def test_concurrency_limit_and_active_worker_lock(tmp_path):
    config, github, children = replace(settings(tmp_path), concurrency=1), Github(), Children()
    github.add(2)
    wait, stop = stop_after(2)
    serve(config, tmp_path, github, children, wait, stop, include_existing=True)
    assert len(children.started) == 1


def test_trace_context_is_not_inherited(monkeypatch):
    monkeypatch.setenv("TRACEPARENT", "parent")
    monkeypatch.setenv("baggage", "something")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    environment = child_environment()
    assert "TRACEPARENT" not in environment and "baggage" not in environment
    assert environment["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://localhost:4318"


def test_config_change_does_not_silently_rebaseline(tmp_path):
    Registry(tmp_path, "me", ["a/b"]).enroll([], False)
    with pytest.raises(Blocked, match="configuration changed"):
        Registry(tmp_path, "another-user", ["a/b"])

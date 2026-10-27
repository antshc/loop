from __future__ import annotations

from dataclasses import replace

import pytest

from workflows.pr_babysit.composition.entry import build_container
from workflows.pr_babysit.discover.children import ChildLauncher, child_environment
from workflows.pr_babysit.discover.registry import Registry
from workflows.pr_babysit.discover.scheduler import Scheduler
from workflows.pr_babysit.model import Blocked, DiscoveryRequest, Pacer, PrState, StopSignal
from workflows.pr_babysit.settings import Repository, Settings
from workflows.pr_babysit.watch.feedback import fingerprint
from workflows.shared.github.account import GitHub
from workflows.shared.telemetry import ObserverFactory

from .support import Pace, bind


def pr(number):
    return {"number": number, "html_url": f"https://github.com/a/b/pull/{number}", "head": {"sha": f"sha{number}"}}


class Github:
    def __init__(self):
        self.prs = [pr(1)]
        self.closed = False
        self.sha = "sha1"

    def login(self):
        return "me"

    def pull_requests(self, slug):
        assert slug == "a/b"
        return self

    def open_by(self, author):
        assert author == "me"
        return self.prs

    def snapshot(self, number):
        return {"pr": {"state": "closed" if self.closed else "open", "head": {"sha": self.sha}},
                "threads": [], "other": [], "checks": [], "statuses": [], "runs": []}


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
        config, bind((GitHub, github), (ChildLauncher, children), (Pacer, Pace(on_wait)), (StopSignal, stop), *extra),
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
        github.prs = [pr(1), pr(2)]
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
        github.sha = "new"
    wait, stop = stop_after(2, change_head)
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
    github.prs.append(pr(2))
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

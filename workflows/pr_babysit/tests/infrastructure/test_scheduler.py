from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from workflows.pr_babysit.infrastructure.github import remote_fingerprint
from workflows.pr_babysit.settings import Repository, Settings
from workflows.pr_babysit.infrastructure.state import Blocked, PrState
from workflows.pr_babysit.infrastructure.state import Registry
from workflows.pr_babysit.infrastructure.scheduler import child_environment, serve


def pr(number):
    return {"number": number, "html_url": f"https://github.com/a/b/pull/{number}", "head": {"sha": f"sha{number}"}}


class Github:
    def __init__(self):
        self.prs = [pr(1)]
        self.closed = False
        self.sha = "sha1"

    def login(self):
        return "me"

    def open_prs(self, repo, author):
        assert author == "me"
        return self.prs

    def snapshot(self, repo, number):
        return {"pr": {"state": "closed" if self.closed else "open", "head": {"sha": self.sha}},
                "threads": [], "other": [], "checks": [], "statuses": [], "runs": []}


class Child:
    returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout):
        return self.returncode


def settings(tmp_path):
    return Settings((Repository("a/b", tmp_path, ("build",), ("test",)),), tmp_path / "state")


def test_first_baseline_then_new_pr_only(tmp_path):
    registry = Registry(tmp_path, "me", ["a/b"])
    registry.enroll([("a/b", pr(1))], False)
    assert PrState(tmp_path, "a/b", 1).data["status"] == "ignored"
    registry = Registry(tmp_path, "me", ["a/b"])
    registry.enroll([("a/b", pr(1)), ("a/b", pr(2))], False)
    assert PrState(tmp_path, "a/b", 2).data["status"] == "queued"
    assert len(registry.data["prs"]) == 2


def test_scheduler_deduplicates_and_starts_new_pr_while_another_runs(tmp_path):
    config, github, spawned = settings(tmp_path), Github(), []
    ticks = 0
    def wait(seconds):
        nonlocal ticks
        ticks += 1
        github.prs = [pr(1), pr(2)]
    def spawn(command, **kwargs):
        spawned.append(command)
        return Child()
    with patch("loop.otlp_observer", side_effect=AssertionError("scheduler initialized telemetry")):
        serve(config, tmp_path / "config.json", include_existing=True, github=github, spawn=spawn,
              wait=wait, stop=lambda: ticks == 3)
    assert len(spawned) == 2
    assert spawned[0][-1].endswith("/1")
    assert spawned[1][-1].endswith("/2")


def test_blocked_pr_resumes_only_after_remote_change(tmp_path):
    config, github = settings(tmp_path), Github()
    registry = Registry(config.state_dir, "me", ["a/b"])
    registry.enroll([("a/b", pr(1))], True)
    state = PrState(config.state_dir, "a/b", 1)
    state.status("blocked", fingerprint=remote_fingerprint(github.snapshot("a/b", 1)))
    ticks, spawned = 0, []
    def wait(seconds):
        nonlocal ticks
        ticks += 1
        github.sha = "new"
    def spawn(*args, **kwargs):
        spawned.append(args)
        return Child()
    serve(config, tmp_path / "config.json", github=github, spawn=spawn, wait=wait, stop=lambda: ticks == 2)
    assert len(spawned) == 1


def test_reopened_enrolled_pr_starts_again(tmp_path):
    config, github = settings(tmp_path), Github()
    registry = Registry(config.state_dir, "me", ["a/b"])
    registry.enroll([("a/b", pr(1))], True)
    PrState(config.state_dir, "a/b", 1).status("completed")
    ticks, spawned = 0, []
    def wait(seconds):
        nonlocal ticks
        ticks += 1
    serve(config, tmp_path / "config.json", github=github,
          spawn=lambda *a, **kw: spawned.append(a) or Child(), wait=wait, stop=lambda: ticks == 1)
    assert len(spawned) == 1


def test_concurrency_limit_and_active_worker_lock(tmp_path):
    config, github = replace(settings(tmp_path), concurrency=1), Github()
    github.prs.append(pr(2))
    ticks, spawned = 0, []
    def wait(seconds):
        nonlocal ticks
        ticks += 1
    serve(config, tmp_path / "config.json", include_existing=True, github=github,
          spawn=lambda *a, **kw: spawned.append(a) or Child(), wait=wait, stop=lambda: ticks == 2)
    assert len(spawned) == 1


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

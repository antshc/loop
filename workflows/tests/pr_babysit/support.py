from __future__ import annotations

import json
import subprocess
import sys

import pytest
from dishka import Provider

from loop import GitCli, GitRuntime, fake_agent
from workflows.pr_babysit.composition.entry import build_container, maintain_pr
from workflows.pr_babysit.model import Pacer, PrTarget
from workflows.pr_babysit.monitor.pr import CheckoutGuard
from workflows.pr_babysit.settings import Repository, Settings
from workflows.pr_babysit.watch.snapshot import babysit_snapshot
from workflows.shared.agents.launcher import AgentFactory
from workflows.shared.github.account import GitHub
from workflows.shared.providers import bind
from workflows.shared.telemetry import Telemetry
from workflows.tests.shared.fakes import (
    FakeGhCli,
    FakePullRequestHost,
    check_run,
    comment_node,
    thread_node,
    workflow_run,
)


def command(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def bare_command(path, *args):
    """Runs git against a bare repository explicitly, so `safe.bareRepository=explicit` cannot refuse it."""
    return subprocess.run(["git", f"--git-dir={path}", *args], check=True, capture_output=True, text=True).stdout.strip()


def comment(id="c1", login="reviewer", body="Check this"):
    return comment_node(id, login, body)


def thread(comments=None, resolved=False):
    return thread_node(comments, resolved=resolved)


class LocalGuard:
    """Accepts the local bare-repo checkout whose origin is a path, not a GitHub slug."""

    def validate(self, path, repo):
        assert path.is_dir()


class Pace:
    """Runs `on_wait` instead of sleeping."""

    def __init__(self, on_wait):
        self._on_wait = on_wait

    def wait(self, seconds):
        self._on_wait(seconds)


class Remote:
    """Pull request a/b#1 on the shared `gh` fake, its head following a local bare repository."""

    def __init__(self, bare, base, *, reviews=True, ci_failure=False):
        self.bare, self.base, self.ci_failure = bare, base, ci_failure
        self.host = FakePullRequestHost()
        self.pull = self.host.add(1, head_sha=lambda: bare_command(bare, "rev-parse", "refs/heads/feature"))
        self.pull.threads = [thread()] if reviews else []
        self.pull.check_runs = self._check_runs
        self.pull.workflow_runs = self._workflow_runs
        self.github = GitHub(FakeGhCli(pulls=self.host))

    @property
    def discussions(self):
        return self.pull.threads

    @discussions.setter
    def discussions(self, threads):
        self.pull.threads = threads

    @property
    def closed(self):
        return self.pull.state == "closed"

    @closed.setter
    def closed(self, value):
        self.pull.close(merged=value)

    @property
    def replies(self):
        return self.host.replies

    @property
    def retries(self):
        return self.host.retries

    def snapshot(self, number):
        return babysit_snapshot(self.github.pull_requests("a/b").snapshot(number))

    def _failing(self):
        return self.ci_failure and self.pull.sha == self.base

    def _check_runs(self):
        return [check_run(1, "test", conclusion="failure" if self._failing() else "success")]

    def _workflow_runs(self):
        return [workflow_run(1, self.pull.sha, conclusion="failure")] if self._failing() else []


class Runner:
    """A scripted agent CLI answering each babysit mode."""

    def __init__(self, *, transient=False, invalid=False):
        self.modes, self.transient, self.invalid = [], transient, invalid

    def run(self, profile, request, turn, context):
        task = json.loads(request.prompt.split("## Task\n")[-1])
        mode = task["mode"]
        self.modes.append(mode)
        if mode == "group":
            output = {"issues": [{"id": "cart", "statement": "duplicate cart entry", "keys": [task["items"][0]["key"]]}]}
        elif mode == "resolve":
            (context.agent.cwd / "cart.py").write_text("fixed = True\n")
            output = {"verdict": "fix-needed", "evidence": ["cart.py:1: duplicate prevented"],
                      "replies": {key: "Fixed!" for key in task["issue"]["keys"]}}
        elif mode == "diagnose":
            output = {"classification": "transient" if self.transient else "branch", "reason": "CI evidence"}
        else:
            (context.agent.cwd / "cart.py").write_text("fixed = True\n")
            output = {"verdict": "fixed", "reason": "fixed cart"}
        return profile.cli.parse("not json" if self.invalid else json.dumps(output), turn, 0)


@pytest.fixture
def scenario(tmp_path):
    bare, checkout = tmp_path / "remote.git", tmp_path / "repo"
    bare.mkdir(); checkout.mkdir()
    command(bare, "init", "--bare")
    command(checkout, "init")
    command(checkout, "config", "user.name", "Test")
    command(checkout, "config", "user.email", "test@example.com")
    (checkout / "cart.py").write_text("fixed = False\n")
    command(checkout, "add", ".")
    command(checkout, "commit", "-m", "initial")
    command(checkout, "branch", "-M", "feature")
    command(checkout, "remote", "add", "origin", str(bare))
    command(checkout, "push", "origin", "feature")
    base = command(checkout, "rev-parse", "HEAD")
    repository = Repository("a/b", checkout, (sys.executable, "-c", "pass"), (sys.executable, "-c", "pass"))
    settings = Settings((repository,), tmp_path / "state", interval=0.01)
    return settings, bare, checkout, base


def fakes(remote, runner, *extra: tuple[type, object], on_wait=None, observer=None) -> Provider:
    """Overrides GitHub, the agent, the checkout guard, the pacer, and telemetry for one scenario."""
    def close(seconds):
        remote.closed = True
    return bind((GitHub, remote.github), (AgentFactory, lambda: fake_agent(runner, git=GitRuntime(GitCli()))),
                (CheckoutGuard, LocalGuard()), (Pacer, Pace(on_wait or close)), (Telemetry, Telemetry(observer)),
                *extra)


def execute(settings, remote, runner, *extra: tuple[type, object], on_wait=None, observer=None) -> int:
    """Maintains PR a/b#1 through the composition root with the scenario's fakes."""
    container = build_container(settings, fakes(remote, runner, *extra, on_wait=on_wait, observer=observer))
    try:
        return maintain_pr(container, PrTarget("a/b", 1))
    finally:
        container.close()

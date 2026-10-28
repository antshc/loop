from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy

import pytest
from dishka import Provider

from loop import GitCli, GitRuntime, fake_agent
from workflows.pr_babysit.composition.entry import build_container, maintain_pr
from workflows.pr_babysit.model import Pacer, PrTarget
from workflows.pr_babysit.monitor.pr import CheckoutGuard
from workflows.pr_babysit.settings import Repository, Settings
from workflows.shared.agents.launcher import AgentFactory
from workflows.shared.github.account import GitHub
from workflows.shared.providers import bind
from workflows.shared.telemetry import Telemetry


def command(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def bare_command(path, *args):
    """Runs git against a bare repository explicitly, so `safe.bareRepository=explicit` cannot refuse it."""
    return subprocess.run(["git", f"--git-dir={path}", *args], check=True, capture_output=True, text=True).stdout.strip()


def comment(id="c1", login="reviewer", body="Check this"):
    return {"id": id, "body": body, "author": {"login": login}, "authorAssociation": "COLLABORATOR"}


def thread(comments=None, resolved=False):
    return {"id": "t1", "isResolved": resolved, "isOutdated": False, "path": "cart.py", "line": 4,
            "comments": comments if comments is not None else [comment()]}


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
    """GitHub and the PR's pull requests, backed by a local bare repository."""

    def __init__(self, bare, base, *, reviews=True, ci_failure=False):
        self.bare, self.base, self.closed = bare, base, False
        self.discussions = [thread()] if reviews else []
        self.replies, self.retries, self.ci_failure = [], [], ci_failure

    def login(self):
        return "me"

    def pull_requests(self, slug):
        assert slug == "a/b"
        return self

    def snapshot(self, number):
        sha = bare_command(self.bare, "rev-parse", "refs/heads/feature")
        failed = self.ci_failure and sha == self.base
        return {"pr": {"number": number, "state": "closed" if self.closed else "open", "merged": self.closed,
                       "head": {"sha": sha, "ref": "feature", "repo": {"full_name": "a/b"}},
                       "user": {"login": "me"}, "draft": False, "mergeable": True, "mergeable_state": "clean"},
                "threads": deepcopy(self.discussions), "other": [], "statuses": [],
                "checks": [{"id": 1, "name": "test", "status": "completed", "conclusion": "failure" if failed else "success"}],
                "runs": [{"id": 1, "workflow_id": 1, "status": "completed", "conclusion": "failure", "head_sha": sha}] if failed else []}

    def pull(self, number):
        return self.snapshot(number)["pr"]

    def reply(self, number, item, body):
        self.replies.append(body)
        self.discussions[0]["comments"].append(comment(f"reply{len(self.replies)}", "me", body))

    def failed_logs(self, runs):
        return "cart.py: duplicate product"

    def rerun_failed(self, run_id):
        self.retries.append(run_id)


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
    return bind((GitHub, remote), (AgentFactory, lambda: fake_agent(runner, git=GitRuntime(GitCli()))),
                (CheckoutGuard, LocalGuard()), (Pacer, Pace(on_wait or close)), (Telemetry, Telemetry(observer)),
                *extra)


def execute(settings, remote, runner, *extra: tuple[type, object], on_wait=None, observer=None) -> int:
    """Maintains PR a/b#1 through the composition root with the scenario's fakes."""
    container = build_container(settings, fakes(remote, runner, *extra, on_wait=on_wait, observer=observer))
    try:
        return maintain_pr(container, PrTarget("a/b", 1))
    finally:
        container.close()

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from loop import FakeCliRunner, GitCli, GitRuntime, fake_agent
from workflows.pr_babysit.infrastructure.git import Git
from workflows.pr_babysit.workflow.run import run
from workflows.pr_babysit.settings import Repository, Settings
from workflows.pr_babysit.infrastructure.state import PrState
from .test_watcher import comment, thread


def command(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


class LocalGit(Git):
    def validate(self, path, repo):
        assert path.is_dir()


class Remote:
    def __init__(self, bare, base, *, reviews=True, ci_failure=False):
        self.bare, self.base, self.closed = bare, base, False
        self.discussions = [thread()] if reviews else []
        self.replies, self.retries, self.ci_failure = [], [], ci_failure

    def login(self):
        return "me"

    def snapshot(self, repo, number):
        sha = command(self.bare, "rev-parse", "refs/heads/feature")
        failed = self.ci_failure and sha == self.base
        return {"pr": {"number": number, "state": "closed" if self.closed else "open", "merged": self.closed,
                       "head": {"sha": sha, "ref": "feature", "repo": {"full_name": repo}},
                       "user": {"login": "me"}, "draft": False, "mergeable": True, "mergeable_state": "clean"},
                "threads": deepcopy(self.discussions), "other": [], "statuses": [],
                "checks": [{"id": 1, "name": "test", "status": "completed", "conclusion": "failure" if failed else "success"}],
                "runs": [{"id": 1, "workflow_id": 1, "status": "completed", "conclusion": "failure", "head_sha": sha}] if failed else []}

    def json(self, *args):
        return self.snapshot("a/b", 1)["pr"]

    def reply(self, repo, number, item, body):
        self.replies.append(body)
        self.discussions[0]["comments"].append(comment(f"reply{len(self.replies)}", "me", body))

    def logs(self, repo, runs):
        return "cart.py: duplicate product"

    def retry(self, repo, run_id):
        self.retries.append(run_id)


class Runner:
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


def execute(settings, remote, runner, *, git=None, observer=None, verification=None):
    def wait(seconds):
        remote.closed = True
    return run(settings, "a/b", 1, github=remote, git=git or LocalGit(), observer=observer,
               new_agent=lambda: fake_agent(runner, git=GitRuntime(GitCli())), wait=wait, verification=verification)


def test_address_verifies_commits_pushes_and_replies_once(scenario):
    settings, bare, checkout, base = scenario
    remote, runner = Remote(bare, base), Runner()
    assert execute(settings, remote, runner) == 0
    assert runner.modes == ["group", "resolve"]
    assert remote.replies == ["Fixed!"]
    assert command(bare, "rev-parse", "feature") != base
    assert command(checkout, "rev-parse", "HEAD") == base
    assert not (settings.state_dir / "worktrees" / "babysit" / "a" / "b" / "1").exists()
    state = PrState(settings.state_dir, "a/b", 1)
    assert state.data["status"] == "completed"
    assert all(item["status"] == "handled" for item in state.data["items"].values())


def test_push_failure_keeps_checkpoint_and_never_replies(scenario):
    settings, bare, checkout, base = scenario
    class RejectPush(LocalGit):
        def publish(self, path, branch):
            raise subprocess.CalledProcessError(1, ["git", "push"])
    remote, runner = Remote(bare, base), Runner()
    assert execute(settings, remote, runner, git=RejectPush()) == 1
    assert not remote.replies
    state = PrState(settings.state_dir, "a/b", 1)
    assert state.data["batch"]["head"] != base
    assert all(item["status"] == "pending" for item in state.data["items"].values())
    assert not (settings.state_dir / "worktrees" / "babysit" / "a" / "b" / "1").exists()
    assert command(checkout, "rev-parse", "babysit/a/b/1") == state.data["batch"]["head"]
    # Recovery recreates the worktree and publishes without another agent call.
    assert execute(settings, remote, runner) == 0
    assert runner.modes == ["group", "resolve"]
    assert remote.replies == ["Fixed!"]


def test_ci_maintainer_fixes_and_python_publishes(scenario):
    settings, bare, checkout, base = scenario
    remote, runner = Remote(bare, base, reviews=False, ci_failure=True), Runner()
    assert execute(settings, remote, runner) == 0
    assert runner.modes == ["diagnose", "fix"]
    assert command(bare, "rev-parse", "feature") != base
    assert not remote.replies


def test_transient_failure_uses_per_sha_budget(scenario):
    settings, bare, checkout, base = scenario
    remote, runner = Remote(bare, base, reviews=False, ci_failure=True), Runner(transient=True)
    assert execute(settings, remote, runner) == 0
    assert remote.retries == [1]
    assert PrState(settings.state_dir, "a/b", 1).data["retries"][base] == 1


def test_invalid_agent_report_does_not_publish(scenario):
    settings, bare, checkout, base = scenario
    remote = Remote(bare, base)
    assert execute(settings, remote, Runner(invalid=True)) == 1
    assert not remote.replies
    assert command(bare, "rev-parse", "feature") == base


def test_failed_verification_keeps_edits_and_does_not_commit_or_reply(scenario):
    settings, bare, checkout, base = scenario
    class FailAfterEdit:
        def __call__(self, path, repository, timeout):
            if (path / "cart.py").read_text() == "fixed = True\n":
                raise subprocess.CalledProcessError(1, ["test"])
    remote = Remote(bare, base)
    assert execute(settings, remote, Runner(), verification=FailAfterEdit()) == 1
    assert command(bare, "rev-parse", "feature") == base
    assert not remote.replies
    retained = settings.state_dir / "worktrees" / "babysit" / "a" / "b" / "1"
    assert (retained / "cart.py").read_text() == "fixed = True\n"


def test_retry_exhaustion_blocks_without_another_rerun(scenario):
    settings, bare, checkout, base = scenario
    state = PrState(settings.state_dir, "a/b", 1)
    state.data["retries"][base] = settings.retries
    state.save()
    remote = Remote(bare, base, reviews=False, ci_failure=True)
    assert execute(settings, remote, Runner(transient=True)) == 1
    assert remote.retries == []


def test_green_pr_receives_late_feedback_without_stopping(scenario):
    settings, bare, checkout, base = scenario
    remote, runner = Remote(bare, base, reviews=False), Runner()
    ticks = 0
    def wait(seconds):
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            remote.discussions = [thread()]
        else:
            remote.closed = True
    assert run(settings, "a/b", 1, github=remote, git=LocalGit(),
               new_agent=lambda: fake_agent(runner, git=GitRuntime(GitCli())), wait=wait) == 0
    assert remote.replies == ["Fixed!"]
    assert runner.modes == ["group", "resolve"]


def test_repeated_stale_ci_snapshot_does_not_spend_another_retry_or_agent_call(scenario):
    settings, bare, checkout, base = scenario
    remote, runner = Remote(bare, base, reviews=False, ci_failure=True), Runner(transient=True)
    ticks = 0
    def wait(seconds):
        nonlocal ticks
        ticks += 1
        if ticks == 3:
            remote.closed = True
    assert run(settings, "a/b", 1, github=remote, git=LocalGit(),
               new_agent=lambda: fake_agent(runner, git=GitRuntime(GitCli())), wait=wait) == 0
    assert remote.retries == [1]
    assert runner.modes == ["diagnose"]


def test_named_worktree_is_serialized_by_workflow(scenario):
    """Given a named worktree is active, when another worker opens it, then it cannot edit."""
    from workflows.pr_babysit.workflow.workspace import open_workspace
    from workflows.pr_babysit.infrastructure.state import Blocked
    settings, bare, checkout, base = scenario
    remote = Remote(bare, base)
    state = PrState(settings.state_dir, "a/b", 1)
    factory = lambda: fake_agent(Runner(), git=GitRuntime(GitCli()))
    args = (settings, settings.repository("a/b"), remote.snapshot("a/b", 1), state, LocalGit(), None, factory)
    with open_workspace(*args) as workspace:
        with pytest.raises(Blocked, match="Another process"):
            with open_workspace(*args):
                pytest.fail("duplicate owner")
        assert workspace.path.exists()

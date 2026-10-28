from __future__ import annotations

import subprocess

import pytest

from workflows.pr_babysit.agents.workspace import PrWorkspace
from workflows.pr_babysit.composition import entry
from workflows.pr_babysit.composition.entry import build_container
from workflows.pr_babysit.model import Blocked, PrState, PrTarget
from workflows.pr_babysit.monitor.pr import PrMonitor
from workflows.pr_babysit.publish.verification import Verifier
from workflows.shared.git import GitClient
from workflows.shared.process import CommandRunner

from workflows.tests.pr_babysit.support import Remote, Runner, bare_command, command, execute, fakes, thread


class RejectPush(GitClient):
    def publish(self, path, branch):
        raise subprocess.CalledProcessError(1, ["git", "push"])


def test_address_verifies_commits_pushes_and_replies_once(scenario):
    settings, bare, checkout, base = scenario
    remote, runner = Remote(bare, base), Runner()
    assert execute(settings, remote, runner) == 0
    assert runner.modes == ["group", "resolve"]
    assert remote.replies == ["Fixed!"]
    assert bare_command(bare, "rev-parse", "feature") != base
    assert command(checkout, "rev-parse", "HEAD") == base
    assert not (settings.state_dir / "worktrees" / "babysit" / "a" / "b" / "1").exists()
    state = PrState(settings.state_dir, "a/b", 1)
    assert state.data["status"] == "completed"
    assert all(item["status"] == "handled" for item in state.data["items"].values())


def test_push_failure_keeps_checkpoint_and_never_replies(scenario):
    settings, bare, checkout, base = scenario
    remote, runner = Remote(bare, base), Runner()
    assert execute(settings, remote, runner, (GitClient, RejectPush(CommandRunner(timeout_s=120)))) == 1
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
    assert bare_command(bare, "rev-parse", "feature") != base
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
    assert bare_command(bare, "rev-parse", "feature") == base


def test_failed_verification_keeps_edits_and_does_not_commit_or_reply(scenario):
    settings, bare, checkout, base = scenario
    class FailAfterEdit:
        def __call__(self, path, repository, timeout):
            if (path / "cart.py").read_text() == "fixed = True\n":
                raise subprocess.CalledProcessError(1, ["test"])
    remote = Remote(bare, base)
    assert execute(settings, remote, Runner(), (Verifier, FailAfterEdit())) == 1
    assert bare_command(bare, "rev-parse", "feature") == base
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
    assert execute(settings, remote, runner, on_wait=wait) == 0
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
    assert execute(settings, remote, runner, on_wait=wait) == 0
    assert remote.retries == [1]
    assert runner.modes == ["diagnose"]


def test_named_worktree_is_serialized_by_workflow(scenario):
    """Given a named worktree is active, when another worker opens it, then it cannot edit."""
    settings, bare, checkout, base = scenario
    remote = Remote(bare, base)
    container = build_container(settings, fakes(remote, Runner()))
    snapshot = remote.snapshot(1)
    try:
        with container(context={PrTarget: PrTarget("a/b", 1)}) as first, \
                container(context={PrTarget: PrTarget("a/b", 1)}) as second:
            with first.get(PrWorkspace).open(snapshot) as workspace:
                with pytest.raises(Blocked, match="Another process"):
                    with second.get(PrWorkspace).open(snapshot):
                        pytest.fail("duplicate owner")
                assert workspace.path.exists()
    finally:
        container.close()


def test_production_graph_resolves_without_overrides(scenario):
    settings, bare, checkout, base = scenario
    container = build_container(settings)
    try:
        with container(context={PrTarget: PrTarget("a/b", 1)}) as scope:
            assert isinstance(scope.get(PrMonitor), PrMonitor)
    finally:
        container.close()


def test_command_maintains_one_pr_with_injected_providers(scenario, tmp_path, monkeypatch):
    settings, bare, checkout, base = scenario
    config = tmp_path / "babysit.json"
    config.write_text(
        '{"repositories": [{"name": "a/b", "path": "repo", "build": ["git", "--version"], "test": ["git", "--version"]}],'
        ' "state_dir": "state", "interval": 0.01}')
    monkeypatch.setattr(entry.signal, "signal", lambda *args: None)
    remote, runner = Remote(bare, base), Runner()
    assert entry.main(["--config", str(config), "--pr", "https://github.com/a/b/pull/1"],
                      providers=[fakes(remote, runner)]) == 0
    assert remote.replies == ["Fixed!"]
    assert PrState(settings.state_dir, "a/b", 1).data["status"] == "completed"


def test_command_reports_unconfigured_repository(scenario, tmp_path, monkeypatch):
    settings, bare, checkout, base = scenario
    config = tmp_path / "babysit.json"
    config.write_text(
        '{"repositories": [{"name": "a/b", "path": "repo", "build": ["b"], "test": ["t"]}], "state_dir": "state"}')
    monkeypatch.setattr(entry.signal, "signal", lambda *args: None)
    assert entry.main(["--config", str(config), "--pr", "https://github.com/c/d/pull/1"],
                      providers=[fakes(Remote(bare, base), Runner())]) == 1

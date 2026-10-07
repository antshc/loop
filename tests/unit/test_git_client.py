from __future__ import annotations

import threading
from pathlib import Path

import pytest

from conftest import FakeRunner, commit_file, git
from loop import Cancelled, CommandError, CommandResult, Hook, HookError, origin_slug
from loop.platforms.git.client import GitClient

CHECKOUT = Path("/repo")
TARGET = Path("/repo.worktrees/feature-x")


def test_remote_branch_exists_reflects_the_show_ref_outcome() -> None:
    runner = FakeRunner()
    runner.returncode_for[("git show-ref --verify --quiet refs/remotes/origin/feature-x", CHECKOUT)] = 1
    client = GitClient(run=runner)

    assert client.remote_branch_exists(CHECKOUT, "feature-x") is False

    runner.returncode_for[("git show-ref --verify --quiet refs/remotes/origin/feature-x", CHECKOUT)] = 0
    assert client.remote_branch_exists(CHECKOUT, "feature-x") is True


def test_run_hook_runs_the_command_on_the_host_in_the_worktree_with_its_timeout() -> None:
    runner = FakeRunner()

    GitClient(run=runner).run_hook(Hook("echo a", timeout_s=5.0), TARGET)

    assert runner.calls == [("echo a", TARGET, 5.0)]


def test_run_hook_raises_hook_error_on_a_non_zero_exit() -> None:
    runner = FakeRunner()
    runner.returncode_for[("setup.sh", TARGET)] = 1
    runner.stderr_for[("setup.sh", TARGET)] = "boom"

    with pytest.raises(HookError, match="setup.sh") as excinfo:
        GitClient(run=runner).run_hook(Hook("setup.sh"), TARGET)

    assert excinfo.value.command == "setup.sh"


def test_run_hook_raises_hook_error_on_timeout() -> None:
    runner = FakeRunner()
    runner.raise_for[("slow.sh", TARGET)] = CommandError("slow.sh", None, "timed out after 1.0s")

    with pytest.raises(HookError, match="timed out"):
        GitClient(run=runner).run_hook(Hook("slow.sh", timeout_s=1.0), TARGET)


def test_run_hook_reports_cancelled_when_the_cancel_event_is_set() -> None:
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(Cancelled):
        GitClient(run=FakeRunner()).run_hook(Hook("setup.sh"), TARGET, cancel)


def test_commit_adds_everything_with_a_subject_and_body() -> None:
    runner = FakeRunner()
    GitClient(run=runner).commit(TARGET, "subject", "body")

    assert runner.calls == [
        ("git add -A", TARGET, None),
        ("git commit -m subject -m body", TARGET, None),
    ]


def test_commit_without_a_body_uses_a_single_message() -> None:
    runner = FakeRunner()
    GitClient(run=runner).commit(TARGET, "subject")

    assert runner.calls[-1] == ("git commit -m subject", TARGET, None)


def test_config_get_reads_a_set_key_and_reports_none_for_an_unset_one(repo: Path) -> None:
    client = GitClient()

    assert client.config_get(repo, "user.name") == "Test"
    assert client.config_get(repo, "no.such") is None


def test_a_failing_git_command_raises_a_typed_error_with_the_command_and_stderr() -> None:
    runner = FakeRunner()
    runner.returncode_for["git check-ref-format --branch feature-x"] = 1
    runner.stderr_for["git check-ref-format --branch feature-x"] = "fatal: no remote"

    with pytest.raises(CommandError, match="fatal: no remote") as excinfo:
        GitClient(run=runner).check_branch_name(CHECKOUT, "feature-x")

    assert excinfo.value.command == "git check-ref-format --branch feature-x"


def test_origin_slug_normalises_ssh_and_https_forms_and_a_trailing_git_suffix() -> None:
    def fixed(url: str) -> object:
        return lambda args, **kwargs: CommandResult(0, url, "")

    ssh = origin_slug(Path("/x"), run=fixed("git@github.com:Owner/Repo.git"))
    https = origin_slug(Path("/x"), run=fixed("https://github.com/owner/repo"))

    assert ssh == "Owner/Repo"
    assert https == "owner/repo"


def test_origin_slug_reports_unresolvable_when_origin_is_missing_or_not_github() -> None:
    missing = origin_slug(Path("/x"), run=lambda args, **kwargs: CommandResult(1, "", "no such remote"))
    other_host = origin_slug(Path("/x"), run=lambda args, **kwargs: CommandResult(0, "https://example.com/a/b", ""))

    assert missing is None
    assert other_host is None


from __future__ import annotations

import threading
from pathlib import Path

import pytest

from conftest import FakeRunner, commit_file, git
from conftest import init_pushed_repo as _init_pushed_repo
from loop import Cancelled, CommandError, CommandResult, GitClient, Hook, HookError, WorktreeService, origin_slug
from loop.testing import FakeGitClient


CHECKOUT = Path("/repo")
TARGET = Path("/repo.worktrees/feature-x")


def test_fetch_fetches_and_prunes_all_remotes() -> None:
    runner = FakeRunner()
    GitClient(run=runner).fetch(CHECKOUT)

    assert runner.calls == [("git fetch --all --prune", CHECKOUT, None)]


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


def test_is_clean_is_true_only_when_the_tree_has_no_changes(repo: Path) -> None:
    client = GitClient()
    assert client.is_clean(repo) is True

    (repo / "README.md").write_text("changed\n")
    assert client.is_clean(repo) is False
    git(repo, "checkout", "--", "README.md")

    (repo / "new.txt").write_text("new\n")
    assert client.is_clean(repo) is False

    git(repo, "add", "new.txt")
    assert client.is_clean(repo) is False


def test_branch_ahead_of_remote_true_with_no_remote_counterpart_but_commits_beyond_base(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    client = GitClient()
    base = git(checkout, "rev-parse", "HEAD")
    git(checkout, "checkout", "-b", "feature-x")
    commit_file(checkout, "a.txt", "1", "work")

    assert client.branch_ahead_of_remote(checkout, "feature-x", base) is True


def test_branch_ahead_of_remote_true_when_the_remote_counterpart_is_behind(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    client = GitClient()
    base = git(checkout, "rev-parse", "HEAD")
    git(checkout, "checkout", "-b", "feature-x")
    commit_file(checkout, "a.txt", "1", "work")
    git(checkout, "push", "origin", "feature-x")
    commit_file(checkout, "b.txt", "2", "more work")
    client.fetch(checkout)

    assert client.branch_ahead_of_remote(checkout, "feature-x", base) is True


def test_branch_ahead_of_remote_false_when_the_remote_holds_every_local_commit(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    client = GitClient()
    base = git(checkout, "rev-parse", "HEAD")
    git(checkout, "checkout", "-b", "feature-x")
    commit_file(checkout, "a.txt", "1", "work")
    git(checkout, "push", "origin", "feature-x")
    client.fetch(checkout)

    assert client.branch_ahead_of_remote(checkout, "feature-x", base) is False


def test_branch_ahead_of_remote_false_when_the_branch_does_not_exist_locally(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    client = GitClient()

    assert client.branch_ahead_of_remote(checkout, "no-such-branch", git(checkout, "rev-parse", "HEAD")) is False


def test_push_is_a_plain_push_with_no_force_flag() -> None:
    runner = FakeRunner()
    GitClient(run=runner).push(TARGET, "feature-x")

    assert runner.calls == [("git push origin feature-x", TARGET, None)]
    assert not any(flag in runner.calls[0][0] for flag in ("--force", " -f"))


def test_a_failing_git_command_raises_a_typed_error_with_the_command_and_stderr() -> None:
    runner = FakeRunner()
    runner.returncode_for["git fetch --all --prune"] = 1
    runner.stderr_for["git fetch --all --prune"] = "fatal: no remote"

    with pytest.raises(CommandError, match="fatal: no remote") as excinfo:
        GitClient(run=runner).fetch(CHECKOUT)

    assert excinfo.value.command == "git fetch --all --prune"


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


def test_branch_primitives_report_head_commits_merge_and_cleanup_against_a_real_repository(
    repo: Path, tmp_path: Path
) -> None:
    client = GitClient()
    worktree = tmp_path / "wt"
    git(repo, "worktree", "add", "-b", "tmp", str(worktree))

    commit_file(worktree, "a.txt", "a\n", "first")
    commit_file(worktree, "b.txt", "b\n", "second")

    assert client.current_branch(repo) == "main" and client.current_branch(worktree) == "tmp"
    assert client.config_get(repo, "user.name") == "Test" and client.config_get(repo, "no.such") is None

    client.merge(repo, "tmp")
    client.detach(worktree)
    client.delete_branch(repo, "tmp")

    assert (repo / "b.txt").exists()
    assert client.current_branch(worktree) is None
    assert git(repo, "branch", "--list", "tmp") == ""


def test_fake_branch_ahead_of_remote() -> None:
    fake = FakeGitClient()
    worktree = WorktreeService(fake).create(CHECKOUT, "feature-x", "main", Path("/harness"))
    base = fake.commits.head(worktree).sha
    fake.commit(worktree, "work")

    assert fake.branch_ahead_of_remote(CHECKOUT, "feature-x", base) is True
    assert fake.branch_ahead_of_remote(CHECKOUT, "no-such-branch", base) is False

    fake.remote_branches.add("feature-x")
    fake.remote_heads["feature-x"] = fake.commits.head(worktree).sha
    assert fake.branch_ahead_of_remote(CHECKOUT, "feature-x", base) is False


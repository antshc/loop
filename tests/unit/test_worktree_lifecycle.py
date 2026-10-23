from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from loop import BranchStrategy, GitCli, GitOptions, GitRuntime, MergeToHeadStrategy


def git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(path: Path, value: str = "initial") -> str:
    (path / "cart.txt").write_text(value)
    git(path, "add", "-A")
    git(path, "commit", "-m", value)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    commit(repo)
    git(repo, "branch", "-M", "host")
    return repo


def runtime():
    return GitRuntime(GitCli())


def options(repo, branch="review"):
    return GitOptions(root_path=repo.parent / "trees", repository_path=repo, strategy=BranchStrategy(branch))


def test_successful_dirty_run_never_auto_commits(repository):
    initial = git(repository, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError, match="uncommitted"):
        with runtime().open(repository, options(repository)) as path:
            (path / "cart.txt").write_text("unverified")
    assert path.exists()
    assert git(path, "rev-parse", "HEAD") == initial
    assert (path / "cart.txt").read_text() == "unverified"


def test_failed_clean_run_removes_directory_but_retains_commits(repository):
    failure = ValueError("push rejected")
    with pytest.raises(ValueError) as caught:
        with runtime().open(repository, options(repository)) as path:
            sha = commit(path, "verified")
            raise failure
    assert caught.value is failure
    assert not path.exists()
    assert git(repository, "rev-parse", "review") == sha
    with runtime().open(repository, options(repository)) as recovered:
        assert git(recovered, "rev-parse", "HEAD") == sha


def test_failed_dirty_run_is_reused_without_losing_original_error(repository):
    failure = ValueError("tests failed")
    with pytest.raises(ValueError) as caught:
        with runtime().open(repository, options(repository)) as path:
            (path / "cart.txt").write_text("unfinished")
            (path / "new.txt").write_text("untracked")
            raise failure
    assert caught.value is failure and path.exists()
    with runtime().open(repository, options(repository)) as recovered:
        assert recovered == path
        assert (path / "new.txt").read_text() == "untracked"
        commit(path, "verified repair")
    assert not path.exists()


def test_cleanup_failure_does_not_mask_run_failure(repository):
    class FailedRemoval(GitCli):
        def remove_worktree(self, repository, target, *, force=False):
            raise OSError("cleanup unavailable")
    failure = ValueError("original failure")
    with pytest.raises(ValueError) as caught:
        with GitRuntime(FailedRemoval()).open(repository, options(repository)):
            raise failure
    assert caught.value is failure


def test_successful_temporary_merge_deletes_branch(repository):
    config = GitOptions(root_path=repository.parent / "trees", strategy=MergeToHeadStrategy())
    with runtime().open(repository, config) as path:
        branch = git(path, "branch", "--show-current")
        sha = commit(path, "verified")
    assert git(repository, "rev-parse", "HEAD") == sha
    assert branch not in git(repository, "branch", "--format=%(refname:short)").splitlines()
    assert not path.exists()


def test_divergent_host_keeps_temporary_branch_for_recovery(repository):
    config = GitOptions(root_path=repository.parent / "trees", strategy=MergeToHeadStrategy())
    with pytest.raises(subprocess.CalledProcessError):
        with runtime().open(repository, config) as path:
            branch = git(path, "branch", "--show-current")
            sha = commit(path, "worktree change")
            commit(repository, "host change")
    assert git(repository, "rev-parse", branch) == sha
    assert not path.exists()
    assert (repository / "cart.txt").read_text() == "host change"


def test_stale_metadata_and_empty_orphan_are_repaired(repository):
    config = options(repository)
    failure = RuntimeError("interrupted")
    with pytest.raises(RuntimeError):
        with runtime().open(repository, config) as path:
            (path / "cart.txt").write_text("unfinished")
            raise failure
    shutil.rmtree(path)
    path.mkdir(parents=True)
    with runtime().open(repository, config) as reopened:
        assert git(reopened, "branch", "--show-current") == "review"
    assert not path.exists()


def test_nonempty_orphan_is_never_deleted(repository):
    config = options(repository)
    target = config.root_path / "review"
    target.mkdir(parents=True)
    (target / "valuable.txt").write_text("keep")
    with pytest.raises(RuntimeError, match="orphan"):
        with runtime().open(repository, config):
            pass
    assert (target / "valuable.txt").read_text() == "keep"


def test_exact_base_wins_over_remote_branch_when_creating_local_branch(repository):
    base = git(repository, "rev-parse", "HEAD")
    later = commit(repository, "later")
    git(repository, "update-ref", "refs/remotes/origin/review", later)
    config = GitOptions(root_path=repository.parent / "trees", strategy=BranchStrategy("review", base))
    with runtime().open(repository, config) as path:
        assert git(path, "rev-parse", "HEAD") == base


def test_existing_wrong_branch_worktree_is_rejected(repository):
    config = options(repository)
    target = config.root_path / "review"
    target.parent.mkdir(parents=True)
    git(repository, "worktree", "add", "-b", "other", str(target))
    with pytest.raises(RuntimeError, match="branch"):
        with runtime().open(repository, config):
            pass
    assert target.exists()


def test_dirty_setup_hook_preserves_files_and_original_failure(repository):
    """Given a setup hook writes edits, when it fails, then the hook error and files survive."""
    from loop import LoopHookError, WorktreeReadyLoopHook
    class FailedSetup(GitCli):
        def run_hook(self, hook, cwd, repository, worktree):
            (cwd / "setup.txt").write_text("unfinished setup")
            raise LoopHookError(hook.point, hook.command, "failed")
    config = GitOptions(root_path=repository.parent / "trees", strategy=BranchStrategy("setup"),
                        loop_hooks=(WorktreeReadyLoopHook("setup"),))
    with pytest.raises(LoopHookError):
        with GitRuntime(FailedSetup()).open(repository, config):
            pytest.fail("agent should not start")
    assert (config.root_path / "setup" / "setup.txt").read_text() == "unfinished setup"


def test_broken_git_link_only_orphan_can_be_recreated(repository):
    """Given an orphan contains only a stale owned link, when opened, then it is recreated."""
    config = options(repository)
    target = config.root_path / "review"
    target.mkdir(parents=True)
    (target / ".git").write_text(f"gitdir: {repository / '.git' / 'worktrees' / 'missing'}\n")
    with runtime().open(repository, config) as path:
        assert git(path, "branch", "--show-current") == "review"
    assert not target.exists()


def test_dry_run_does_not_remove_an_empty_orphan_directory(repository):
    """Given an existing empty directory, when dry-running, then no filesystem cleanup occurs."""
    from loop import LoggingGitCli
    config = options(repository)
    target = config.root_path / "review"
    target.mkdir(parents=True)
    with GitRuntime(LoggingGitCli()).open(repository, config):
        pass
    assert target.exists()


def test_administratively_locked_worktree_is_not_reused(repository):
    """Given a retained worktree is locked, when opened, then its owner keeps control."""
    config = options(repository)
    with pytest.raises(ValueError):
        with runtime().open(repository, config) as path:
            (path / "cart.txt").write_text("unfinished")
            raise ValueError("interrupted")
    git(repository, "worktree", "lock", str(path))
    with pytest.raises(RuntimeError, match="locked"):
        with runtime().open(repository, config):
            pytest.fail("locked worktree reused")
    assert (path / "cart.txt").read_text() == "unfinished"

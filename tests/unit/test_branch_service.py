from __future__ import annotations

from pathlib import Path

import pytest

from conftest import commit_file, git
from conftest import init_pushed_repo as _init_pushed_repo
from loop import Branch, BranchService, CommandError, GitClient


def _origin_branch(checkout: Path, name: str, *, base: str = "main") -> None:
    """Creates `name` on `origin` at a fresh commit, leaving no local branch behind."""
    git(checkout, "checkout", "-b", name, f"origin/{base}")
    commit_file(checkout, f"{name}.txt", "work\n", f"{name} work")
    git(checkout, "push", "origin", name)
    git(checkout, "checkout", base)
    git(checkout, "branch", "-D", name)


def test_prepare_points_the_local_branch_at_the_branch_origin_counterpart_when_it_exists(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    _origin_branch(checkout, "feature-x")
    service = BranchService(GitClient())

    prepared = service.prepare(Branch(checkout, "feature-x"), Branch(checkout, "main"))

    assert prepared == Branch(checkout, "feature-x")
    assert git(checkout, "rev-parse", "feature-x") == git(checkout, "rev-parse", "origin/feature-x")


def test_prepare_points_the_local_branch_at_the_base_origin_counterpart_when_missing_on_origin(
    tmp_path: Path,
) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    service = BranchService(GitClient())

    prepared = service.prepare(Branch(checkout, "feature-y"), Branch(checkout, "main"))

    assert prepared == Branch(checkout, "feature-y")
    assert git(checkout, "rev-parse", "feature-y") == git(checkout, "rev-parse", "origin/main")


def test_prepare_moves_an_existing_unchecked_out_local_branch_to_the_base_origin_counterpart(
    tmp_path: Path,
) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    git(checkout, "branch", "feature-z")
    commit_file(checkout, "more.txt", "more\n", "more work")
    git(checkout, "push", "origin", "main")
    service = BranchService(GitClient())

    prepared = service.prepare(Branch(checkout, "feature-z"), Branch(checkout, "main"))

    assert prepared == Branch(checkout, "feature-z")
    assert git(checkout, "rev-parse", "feature-z") == git(checkout, "rev-parse", "origin/main")


def test_prepare_generates_a_unique_sandbox_name_when_no_branch_is_given(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    service = BranchService(GitClient())

    prepared = service.prepare(None, Branch(checkout, "main"))

    assert prepared.name.startswith("loop/sandbox-")
    assert git(checkout, "rev-parse", prepared.name) == git(checkout, "rev-parse", "origin/main")


def test_prepare_keeps_an_unpublished_commit_when_the_branch_is_already_checked_out_at_the_target(
    tmp_path: Path,
) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    target = tmp_path / "worktree"
    git(checkout, "branch", "feature-x")
    git(checkout, "worktree", "add", str(target), "feature-x")
    commit_file(target, "unpublished.txt", "wip\n", "unpublished work")
    before = git(target, "rev-parse", "feature-x")
    service = BranchService(GitClient())

    prepared = service.prepare(Branch(checkout, "feature-x"), Branch(checkout, "main"), target)

    assert prepared == Branch(checkout, "feature-x")
    assert git(checkout, "rev-parse", "feature-x") == before


def test_prepare_rejects_an_invalid_branch_name_and_changes_nothing(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    service = BranchService(GitClient())

    with pytest.raises(CommandError):
        service.prepare(Branch(checkout, ".. bad name"), Branch(checkout, "main"))

    assert git(checkout, "branch", "--list").strip() == "* main"


def test_ahead_of_remote_is_true_when_the_branch_has_commits_its_origin_counterpart_lacks(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    git(checkout, "checkout", "-b", "feature-x")
    git(checkout, "push", "origin", "feature-x")
    commit_file(checkout, "a.txt", "1\n", "work")
    service = BranchService(GitClient())

    assert service.ahead_of_remote(Branch(checkout, "feature-x"), Branch(checkout, "main")) is True


def test_ahead_of_remote_is_true_beyond_the_base_counterpart_when_the_branch_has_no_origin_counterpart(
    tmp_path: Path,
) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    git(checkout, "checkout", "-b", "feature-x")
    commit_file(checkout, "a.txt", "1\n", "work")
    service = BranchService(GitClient())

    assert service.ahead_of_remote(Branch(checkout, "feature-x"), Branch(checkout, "main")) is True


def test_ahead_of_remote_is_false_when_no_local_branch_exists(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    service = BranchService(GitClient())

    assert service.ahead_of_remote(Branch(checkout, "no-such-branch"), Branch(checkout, "main")) is False


def test_push_publishes_the_local_branch_commit_to_origin(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    git(checkout, "checkout", "-b", "feature-x")
    commit_file(checkout, "a.txt", "1\n", "work")
    service = BranchService(GitClient())

    service.push(Branch(checkout, "feature-x"))

    git(checkout, "fetch", "origin")
    assert git(checkout, "rev-parse", "feature-x") == git(checkout, "rev-parse", "origin/feature-x")


def test_merge_brings_the_branch_commits_into_the_target_checkout_without_an_editor(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    git(checkout, "checkout", "-b", "feature-x")
    commit_file(checkout, "a.txt", "1\n", "feature work")
    git(checkout, "checkout", "main")
    commit_file(checkout, "b.txt", "2\n", "main work")
    service = BranchService(GitClient())

    service.merge(checkout, Branch(checkout, "feature-x"))

    assert (checkout / "a.txt").exists() and (checkout / "b.txt").exists()


def test_delete_removes_the_local_branch(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    git(checkout, "branch", "feature-x")
    service = BranchService(GitClient())

    service.delete(Branch(checkout, "feature-x"))

    assert git(checkout, "branch", "--list", "feature-x").strip() == ""


def test_fetch_prunes_remote_branches_deleted_on_origin(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    git(checkout, "checkout", "-b", "feature-x")
    git(checkout, "push", "origin", "feature-x")
    git(checkout, "checkout", "main")
    git(checkout, "fetch", "origin")
    assert "origin/feature-x" in git(checkout, "branch", "-r")

    remote = checkout.parent / f"{checkout.name}-remote.git"
    git(remote, "branch", "-D", "feature-x")
    service = BranchService(GitClient())

    service.fetch(checkout)

    assert "origin/feature-x" not in git(checkout, "branch", "-r")

from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import commit_file, git
from conftest import init_pushed_repo as _init_pushed_repo
from loop import Branch, CommandError, Worktree, WorktreeService


def _service() -> WorktreeService:
    return WorktreeService()


def _local_branch(checkout: Path, name: str, start: str = "main") -> Branch:
    """Creates a local branch ref at `start` with real git, without checking it out."""
    git(checkout, "branch", name, start)
    return Branch(checkout, name)


def test_create_attaches_a_worktree_at_an_arbitrary_target_outside_the_checkout(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    branch = _local_branch(checkout, "feature-x")
    target = tmp_path / "elsewhere" / "feature-x"

    worktree = _service().create(branch, target)

    assert worktree == Worktree(target, branch)
    assert target.is_dir()
    assert git(target, "rev-parse", "--abbrev-ref", "HEAD") == "feature-x"


def test_create_reuses_the_worktree_already_registered_with_the_same_branch_at_the_target(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    branch = _local_branch(checkout, "feature-x")
    target = tmp_path / "feature-x"
    service = _service()
    first = service.create(branch, target)

    second = service.create(branch, target)

    assert second == first
    assert [w.path for w in service.list(checkout)].count(target) == 1


def test_create_replaces_a_clean_leftover_worktree_registered_with_a_different_branch(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    old = _local_branch(checkout, "old")
    new = _local_branch(checkout, "feature-x")
    target = tmp_path / "feature-x"
    service = _service()
    service.create(old, target)

    worktree = service.create(new, target)

    assert worktree == Worktree(target, new)
    assert git(target, "rev-parse", "--abbrev-ref", "HEAD") == "feature-x"


def test_create_rejects_a_dirty_leftover_worktree_and_leaves_it_in_place(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    old = _local_branch(checkout, "old")
    new = _local_branch(checkout, "feature-x")
    target = tmp_path / "feature-x"
    service = _service()
    service.create(old, target)
    (target / "dirty.txt").write_text("oops\n")

    with pytest.raises(CommandError, match=re.escape(str(target))):
        service.create(new, target)

    assert target.is_dir()
    assert git(target, "rev-parse", "--abbrev-ref", "HEAD") == "old"


def test_create_rejects_a_branch_checked_out_in_another_worktree(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    branch = _local_branch(checkout, "feature-x")
    service = _service()
    elsewhere = service.create(branch, tmp_path / "elsewhere")

    with pytest.raises(CommandError, match=re.escape(str(elsewhere.path))):
        service.create(branch, tmp_path / "another-target")

    assert not (tmp_path / "another-target").exists()


def test_list_reports_every_worktree_with_its_branch_or_none_when_its_head_is_detached(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    branch = _local_branch(checkout, "feature-x")
    service = _service()
    worktree = service.create(branch, tmp_path / "feature-x")
    git(worktree.path, "checkout", "--detach")

    by_path = {w.path: w.branch for w in service.list(checkout)}

    assert by_path[checkout] == Branch(checkout, "main")
    assert by_path[worktree.path] is None


def test_get_finds_a_worktree_through_its_own_repository(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    branch = _local_branch(checkout, "feature-x")
    service = _service()
    worktree = service.create(branch, tmp_path / "feature-x")

    assert service.get(worktree.path) == worktree


def test_detach_detaches_the_worktree_head_and_returns_it_with_no_branch(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    branch = _local_branch(checkout, "feature-x")
    service = _service()
    worktree = service.create(branch, tmp_path / "feature-x")

    detached = service.detach(worktree)

    assert detached == Worktree(worktree.path, None)
    assert service.get(worktree.path).branch is None


def test_has_changes_and_is_clean_reflect_the_worktree_working_tree(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    branch = _local_branch(checkout, "feature-x")
    service = _service()
    worktree = service.create(branch, tmp_path / "feature-x")

    assert service.has_changes(worktree) is False
    assert service.is_clean(worktree) is True

    (worktree.path / "new.txt").write_text("content\n")

    assert service.has_changes(worktree) is True
    assert service.is_clean(worktree) is False


def test_remove_deletes_a_clean_worktree_without_force(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    branch = _local_branch(checkout, "feature-x")
    service = _service()
    worktree = service.create(branch, tmp_path / "feature-x")

    service.remove(worktree)

    assert not worktree.path.exists()
    assert git(checkout, "branch", "--list", "feature-x").strip() == "feature-x"


def test_remove_requires_force_to_delete_a_dirty_worktree(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    branch = _local_branch(checkout, "feature-x")
    service = _service()
    worktree = service.create(branch, tmp_path / "feature-x")
    (worktree.path / "dirty.txt").write_text("oops\n")

    with pytest.raises(CommandError):
        service.remove(worktree)
    assert worktree.path.exists()

    service.remove(worktree, force=True)

    assert not worktree.path.exists()


def test_create_commit_push_and_remove_against_a_real_repository(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "checkout")
    branch = _local_branch(checkout, "feature-x")
    service = _service()
    target = tmp_path / "feature-x"

    worktree = service.create(branch, target)
    commit_file(worktree.path, "new.txt", "content\n", "worktree commit")
    git(checkout, "fetch", "origin")

    assert worktree == Worktree(target, branch)
    assert git(worktree.path, "rev-parse", "HEAD") != git(checkout, "rev-parse", "main")

    service.remove(worktree)

    assert not worktree.path.exists()
    assert git(checkout, "branch", "--list", "feature-x").strip() == "feature-x"


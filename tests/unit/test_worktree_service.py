from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import FakeRunner, commit_file, git
from conftest import init_pushed_repo as _init_pushed_repo
from loop import CommandError, GitClient, Hook, WorktreeService

CHECKOUT = Path("/repo")


def _target(harness_root: Path) -> Path:
    return harness_root / "workspace" / "repo.worktrees" / "feature-x"


def _service(runner: FakeRunner) -> WorktreeService:
    return WorktreeService(GitClient(run=runner))


def _no_leftovers_no_remote_branches(runner: FakeRunner) -> None:
    runner.stdout_for["git worktree list --porcelain"] = ""
    runner.returncode_for[("git show-ref --verify --quiet refs/remotes/origin/feature-x", CHECKOUT)] = 1
    runner.returncode_for[("git show-ref --verify --quiet refs/heads/feature-x", CHECKOUT)] = 1


def test_new_branch_names_are_prefixed_and_unique() -> None:
    first, second = WorktreeService.new_branch_name(), WorktreeService.new_branch_name()

    assert re.fullmatch(r"loop/sandbox-[0-9a-f]{8}", first)
    assert first != second


def test_worktree_path_sits_under_the_harness_workspace_folder_named_after_the_checkout() -> None:
    path = WorktreeService.worktree_path(Path("/clones/widgets"), Path("/harness"), "feature/x")

    assert path == Path("/harness/workspace/widgets.worktrees/feature/x")


def test_feature_branch_name_prefixes_the_slug_with_an_underscored_version() -> None:
    assert WorktreeService.feature_branch_name("release/2.4", "Add Login Page!") == "2_4_add-login-page"


def test_feature_branch_name_is_just_the_slug_without_a_version() -> None:
    assert WorktreeService.feature_branch_name("main", "Add Login Page!") == "add-login-page"


def test_create_bases_on_the_remote_feature_branch_when_it_exists(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    runner.stdout_for["git worktree list --porcelain"] = ""
    runner.returncode_for[("git show-ref --verify --quiet refs/remotes/origin/feature-x", CHECKOUT)] = 0
    runner.returncode_for[("git show-ref --verify --quiet refs/heads/feature-x", CHECKOUT)] = 1

    result = _service(runner).create(CHECKOUT, "feature-x", "main", tmp_path)

    assert result == target
    labels = [label for label, _, _ in runner.calls]
    assert f"git worktree add -b feature-x {target} origin/feature-x" in labels
    assert not any(label.startswith("git branch -f") for label in labels)


def test_create_bases_on_the_remote_target_branch_and_resets_an_existing_local_branch(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    runner.stdout_for["git worktree list --porcelain"] = ""
    runner.returncode_for[("git show-ref --verify --quiet refs/remotes/origin/feature-x", CHECKOUT)] = 1
    runner.returncode_for[("git show-ref --verify --quiet refs/heads/feature-x", CHECKOUT)] = 0

    _service(runner).create(CHECKOUT, "feature-x", "main", tmp_path)

    labels = [label for label, _, _ in runner.calls]
    assert "git branch -f feature-x origin/main" in labels
    assert f"git worktree add {target} feature-x" in labels


def test_create_rejects_a_branch_checked_out_in_another_worktree(tmp_path: Path) -> None:
    runner = FakeRunner()
    other = Path("/repo.worktrees/old-feature-x")
    runner.stdout_for["git worktree list --porcelain"] = (
        f"worktree {other}\nHEAD 1111111111111111111111111111111111111111\nbranch refs/heads/feature-x\n"
    )

    with pytest.raises(CommandError, match=r"/repo\.worktrees/old-feature-x"):
        _service(runner).create(CHECKOUT, "feature-x", "main", tmp_path)

    assert not any("worktree add" in label for label, _, _ in runner.calls)


def test_create_replaces_a_clean_leftover_worktree(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    runner.stdout_for["git worktree list --porcelain"] = (
        f"worktree {target}\nHEAD 1111111111111111111111111111111111111111\nbranch refs/heads/feature-x\n"
    )
    runner.stdout_for[("git status --porcelain", target)] = ""
    runner.returncode_for[("git show-ref --verify --quiet refs/remotes/origin/feature-x", CHECKOUT)] = 1
    runner.returncode_for[("git show-ref --verify --quiet refs/heads/feature-x", CHECKOUT)] = 1

    result = _service(runner).create(CHECKOUT, "feature-x", "main", tmp_path)

    assert result == target
    labels = [label for label, _, _ in runner.calls]
    assert f"git worktree remove --force {target}" in labels
    assert f"git worktree add -b feature-x {target} origin/main" in labels


def test_create_rejects_a_dirty_leftover_worktree_and_leaves_it_in_place(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    runner.stdout_for["git worktree list --porcelain"] = (
        f"worktree {target}\nHEAD 1111111111111111111111111111111111111111\nbranch refs/heads/feature-x\n"
    )
    runner.stdout_for[("git status --porcelain", target)] = " M dirty.txt\n"

    with pytest.raises(CommandError, match=re.escape(str(target))):
        _service(runner).create(CHECKOUT, "feature-x", "main", tmp_path)

    assert not any("worktree remove" in label for label, _, _ in runner.calls)


def test_remove_keeps_the_local_branch_and_issues_no_remote_command(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    _no_leftovers_no_remote_branches(runner)
    service = _service(runner)
    service.create(CHECKOUT, "feature-x", "main", tmp_path)

    service.remove(target)

    labels = [label for label, _, _ in runner.calls]
    assert labels[-1] == f"git worktree remove --force {target}"
    assert not any("push" in label or "branch -d" in label or "branch -D" in label for label in labels)


def test_remove_rejects_an_unknown_worktree() -> None:
    runner = FakeRunner()

    with pytest.raises(CommandError, match="unknown worktree"):
        _service(runner).remove(Path("/not/tracked"))

    assert runner.calls == []


def test_create_commit_push_and_remove_against_a_real_repository(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-b", "main")

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git(checkout, "init", "-b", "main")
    git(checkout, "config", "user.email", "test@example.com")
    git(checkout, "config", "user.name", "Test")
    commit_file(checkout, "README.md", "hello\n", "initial")
    git(checkout, "remote", "add", "origin", str(remote))
    git(checkout, "push", "origin", "main")

    client = GitClient()
    service = WorktreeService(client)
    client.fetch(checkout)

    worktree = service.create(checkout, "feature/x", "main", checkout)
    client.run_hook(Hook("touch .ready"), worktree)

    assert worktree == checkout / "workspace" / "checkout.worktrees" / "feature/x"
    assert (worktree / ".ready").exists()
    assert client.has_changes(worktree)

    client.commit(worktree, "worktree ready")
    assert not client.has_changes(worktree)

    (worktree / "new.txt").write_text("content\n")
    assert client.has_changes(worktree)

    client.commit(worktree, "add new file", "body text")
    assert not client.has_changes(worktree)

    client.push(worktree, "feature/x")
    assert git(worktree, "rev-parse", "HEAD") == git(remote, "rev-parse", "feature/x")

    service.remove(worktree)

    assert not worktree.exists()
    assert git(checkout, "branch", "--list", "feature/x").strip() == "feature/x"


def test_create_places_a_single_repo_worktree_under_the_harness_workspace_folder_and_excludes_it(
    tmp_path: Path,
) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")

    worktree = WorktreeService(GitClient()).create(checkout, "feature-x", "main", checkout)

    assert worktree == checkout / "workspace" / "harness.worktrees" / "feature-x"
    assert worktree.is_dir()
    exclude = (checkout / ".git" / "info" / "exclude").read_text().splitlines()
    assert "workspace/" in exclude


def test_create_places_a_multi_repo_worktree_beside_the_clone_inside_the_workspace_folder(tmp_path: Path) -> None:
    harness_root = tmp_path / "harness"
    harness_root.mkdir()
    git(harness_root, "init", "-b", "main")
    clone = _init_pushed_repo(harness_root / "workspace" / "widgets")

    worktree = WorktreeService(GitClient()).create(clone, "feature-x", "main", harness_root)

    assert worktree == harness_root / "workspace" / "widgets.worktrees" / "feature-x"
    assert worktree.is_dir()


def test_create_excludes_the_workspace_folder_exactly_once_across_repeated_creations(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    service = WorktreeService(GitClient())

    service.create(checkout, "feature-x", "main", checkout)
    service.create(checkout, "feature-y", "main", checkout)

    exclude = (checkout / ".git" / "info" / "exclude").read_text().splitlines()
    assert exclude.count("workspace/") == 1


def test_create_modifies_no_tracked_file_of_the_checkout(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")

    WorktreeService(GitClient()).create(checkout, "feature-x", "main", checkout)

    assert git(checkout, "status", "--porcelain") == ""

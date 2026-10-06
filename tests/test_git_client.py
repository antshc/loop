from __future__ import annotations

import re
from pathlib import Path

import pytest

from conftest import commit_file, git
from orb import CommandError, CommandResult, GitClient, Hook, HookError, origin_slug, same_slug


class FakeRunner:
    """Records every command GitClient issues and returns a scripted result for it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Path | None, float | None]] = []
        self.returncode_for: dict[object, int] = {}
        self.stdout_for: dict[object, str] = {}
        self.stderr_for: dict[object, str] = {}
        self.raise_for: dict[object, Exception] = {}

    def __call__(
        self, args: tuple[str, ...] | str, *, cwd: Path | None = None, timeout_s: float | None = None
    ) -> CommandResult:
        label = args if isinstance(args, str) else " ".join(args)
        self.calls.append((label, cwd, timeout_s))
        key = (label, cwd)
        if key in self.raise_for:
            raise self.raise_for[key]
        if label in self.raise_for:
            raise self.raise_for[label]
        returncode = self.returncode_for.get(key, self.returncode_for.get(label, 0))
        stdout = self.stdout_for.get(key, self.stdout_for.get(label, ""))
        stderr = self.stderr_for.get(key, self.stderr_for.get(label, ""))
        return CommandResult(returncode, stdout, stderr)


CHECKOUT = Path("/repo")
TARGET = Path("/repo.worktrees/feature-x")


def _target(harness_root: Path) -> Path:
    return harness_root / "workspace" / "repo.worktrees" / "feature-x"


def _no_leftovers_no_remote_branches(runner: FakeRunner) -> None:
    runner.stdout_for["git worktree list --porcelain"] = ""
    runner.returncode_for[("git show-ref --verify --quiet refs/remotes/origin/feature-x", CHECKOUT)] = 1
    runner.returncode_for[("git show-ref --verify --quiet refs/heads/feature-x", CHECKOUT)] = 1


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


def test_create_worktree_bases_on_the_remote_feature_branch_when_it_exists(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    runner.stdout_for["git worktree list --porcelain"] = ""
    runner.returncode_for[("git show-ref --verify --quiet refs/remotes/origin/feature-x", CHECKOUT)] = 0
    runner.returncode_for[("git show-ref --verify --quiet refs/heads/feature-x", CHECKOUT)] = 1
    client = GitClient(run=runner)

    result = client.create_worktree(CHECKOUT, "feature-x", "main", tmp_path)

    assert result == target
    labels = [label for label, _, _ in runner.calls]
    assert f"git worktree add -b feature-x {target} origin/feature-x" in labels
    assert not any(label.startswith("git branch -f") for label in labels)


def test_create_worktree_bases_on_the_remote_target_branch_and_resets_an_existing_local_branch(
    tmp_path: Path,
) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    runner.stdout_for["git worktree list --porcelain"] = ""
    runner.returncode_for[("git show-ref --verify --quiet refs/remotes/origin/feature-x", CHECKOUT)] = 1
    runner.returncode_for[("git show-ref --verify --quiet refs/heads/feature-x", CHECKOUT)] = 0
    client = GitClient(run=runner)

    client.create_worktree(CHECKOUT, "feature-x", "main", tmp_path)

    labels = [label for label, _, _ in runner.calls]
    assert "git branch -f feature-x origin/main" in labels
    assert f"git worktree add {target} feature-x" in labels


def test_create_worktree_rejects_a_branch_checked_out_in_another_worktree(tmp_path: Path) -> None:
    runner = FakeRunner()
    other = Path("/repo.worktrees/old-feature-x")
    runner.stdout_for["git worktree list --porcelain"] = (
        f"worktree {other}\nHEAD 1111111111111111111111111111111111111111\nbranch refs/heads/feature-x\n"
    )
    client = GitClient(run=runner)

    with pytest.raises(CommandError, match=r"/repo\.worktrees/old-feature-x"):
        client.create_worktree(CHECKOUT, "feature-x", "main", tmp_path)

    assert not any("worktree add" in label for label, _, _ in runner.calls)


def test_create_worktree_replaces_a_clean_leftover_worktree(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    runner.stdout_for["git worktree list --porcelain"] = (
        f"worktree {target}\nHEAD 1111111111111111111111111111111111111111\nbranch refs/heads/feature-x\n"
    )
    runner.stdout_for[("git status --porcelain", target)] = ""
    runner.returncode_for[("git show-ref --verify --quiet refs/remotes/origin/feature-x", CHECKOUT)] = 1
    runner.returncode_for[("git show-ref --verify --quiet refs/heads/feature-x", CHECKOUT)] = 1
    client = GitClient(run=runner)

    result = client.create_worktree(CHECKOUT, "feature-x", "main", tmp_path)

    assert result == target
    labels = [label for label, _, _ in runner.calls]
    assert f"git worktree remove --force {target}" in labels
    assert f"git worktree add -b feature-x {target} origin/main" in labels


def test_create_worktree_rejects_a_dirty_leftover_worktree_and_leaves_it_in_place(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    runner.stdout_for["git worktree list --porcelain"] = (
        f"worktree {target}\nHEAD 1111111111111111111111111111111111111111\nbranch refs/heads/feature-x\n"
    )
    runner.stdout_for[("git status --porcelain", target)] = " M dirty.txt\n"
    client = GitClient(run=runner)

    with pytest.raises(CommandError, match=re.escape(str(target))):
        client.create_worktree(CHECKOUT, "feature-x", "main", tmp_path)

    assert not any("worktree remove" in label for label, _, _ in runner.calls)


def test_create_worktree_runs_hooks_on_the_host_in_the_worktree_in_declared_order(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    _no_leftovers_no_remote_branches(runner)
    client = GitClient(run=runner)

    client.create_worktree(CHECKOUT, "feature-x", "main", tmp_path, on_ready=(Hook("echo a"), Hook("echo b")))

    hook_calls = [(label, cwd) for label, cwd, _ in runner.calls if label in ("echo a", "echo b")]
    assert hook_calls == [("echo a", target), ("echo b", target)]


def test_create_worktree_removes_the_worktree_and_raises_hook_error_on_a_non_zero_exit(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    _no_leftovers_no_remote_branches(runner)
    runner.returncode_for[("setup.sh", target)] = 1
    runner.stderr_for[("setup.sh", target)] = "boom"
    client = GitClient(run=runner)

    with pytest.raises(HookError, match="setup.sh") as excinfo:
        client.create_worktree(CHECKOUT, "feature-x", "main", tmp_path, on_ready=(Hook("setup.sh"),))

    assert excinfo.value.command == "setup.sh"
    labels = [label for label, _, _ in runner.calls]
    assert labels[-1] == f"git worktree remove --force {target}"


def test_create_worktree_removes_the_worktree_and_raises_hook_error_on_timeout(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    _no_leftovers_no_remote_branches(runner)
    runner.raise_for[("slow.sh", target)] = CommandError("slow.sh", None, "timed out after 1.0s")
    client = GitClient(run=runner)

    with pytest.raises(HookError, match="timed out"):
        client.create_worktree(CHECKOUT, "feature-x", "main", tmp_path, on_ready=(Hook("slow.sh", timeout_s=1.0),))

    labels = [label for label, _, _ in runner.calls]
    assert labels[-1] == f"git worktree remove --force {target}"


def test_preparing_the_same_branch_twice_runs_the_hooks_both_times(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    runner.returncode_for[("git show-ref --verify --quiet refs/remotes/origin/feature-x", CHECKOUT)] = 1
    runner.returncode_for[("git show-ref --verify --quiet refs/heads/feature-x", CHECKOUT)] = 1
    client = GitClient(run=runner)

    runner.stdout_for["git worktree list --porcelain"] = ""
    client.create_worktree(CHECKOUT, "feature-x", "main", tmp_path, on_ready=(Hook("setup.sh"),))

    runner.stdout_for["git worktree list --porcelain"] = (
        f"worktree {target}\nHEAD 1111111111111111111111111111111111111111\nbranch refs/heads/feature-x\n"
    )
    runner.stdout_for[("git status --porcelain", target)] = ""
    client.create_worktree(CHECKOUT, "feature-x", "main", tmp_path, on_ready=(Hook("setup.sh"),))

    assert [label for label, _, _ in runner.calls if label == "setup.sh"] == ["setup.sh", "setup.sh"]


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


def test_push_is_a_plain_push_with_no_force_flag() -> None:
    runner = FakeRunner()
    GitClient(run=runner).push(TARGET, "feature-x")

    assert runner.calls == [("git push origin feature-x", TARGET, None)]
    assert not any(flag in runner.calls[0][0] for flag in ("--force", " -f"))


def test_remove_worktree_keeps_the_local_branch_and_issues_no_remote_command(tmp_path: Path) -> None:
    target = _target(tmp_path)
    runner = FakeRunner()
    _no_leftovers_no_remote_branches(runner)
    client = GitClient(run=runner)
    client.create_worktree(CHECKOUT, "feature-x", "main", tmp_path)

    client.remove_worktree(target)

    labels = [label for label, _, _ in runner.calls]
    assert labels[-1] == f"git worktree remove --force {target}"
    assert not any("push" in label or "branch -d" in label or "branch -D" in label for label in labels)


def test_remove_worktree_rejects_an_unknown_worktree() -> None:
    runner = FakeRunner()
    with pytest.raises(CommandError, match="unknown worktree"):
        GitClient(run=runner).remove_worktree(Path("/not/tracked"))

    assert runner.calls == []


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
    assert same_slug(ssh, https)


def test_origin_slug_reports_unresolvable_when_origin_is_missing_or_not_github() -> None:
    missing = origin_slug(Path("/x"), run=lambda args, **kwargs: CommandResult(1, "", "no such remote"))
    other_host = origin_slug(Path("/x"), run=lambda args, **kwargs: CommandResult(0, "https://example.com/a/b", ""))

    assert missing is None
    assert other_host is None


def test_same_slug_is_false_when_either_side_is_unresolvable() -> None:
    assert same_slug(None, "owner/repo") is False
    assert same_slug("owner/repo", None) is False


def _init_pushed_repo(path: Path) -> Path:
    """Real git repo at `path` with a committed `main` pushed to a bare `origin`."""
    remote = path.parent / f"{path.name}-remote.git"
    remote.mkdir(parents=True)
    git(remote, "init", "--bare", "-b", "main")
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    commit_file(path, "README.md", "hello\n", "initial")
    git(path, "remote", "add", "origin", str(remote))
    git(path, "push", "origin", "main")
    return path


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
    client.fetch(checkout)

    worktree = client.create_worktree(checkout, "feature/x", "main", checkout, on_ready=(Hook("touch .ready"),))

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

    client.remove_worktree(worktree)

    assert not worktree.exists()
    assert git(checkout, "branch", "--list", "feature/x").strip() == "feature/x"


def test_create_worktree_places_a_single_repo_worktree_under_the_harness_workspace_folder_and_excludes_it(
    tmp_path: Path,
) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    client = GitClient()

    worktree = client.create_worktree(checkout, "feature-x", "main", checkout)

    assert worktree == checkout / "workspace" / "harness.worktrees" / "feature-x"
    assert worktree.is_dir()
    exclude = (checkout / ".git" / "info" / "exclude").read_text().splitlines()
    assert "workspace/" in exclude


def test_create_worktree_places_a_multi_repo_worktree_beside_the_clone_inside_the_workspace_folder(
    tmp_path: Path,
) -> None:
    harness_root = tmp_path / "harness"
    harness_root.mkdir()
    git(harness_root, "init", "-b", "main")
    clone = _init_pushed_repo(harness_root / "workspace" / "widgets")
    client = GitClient()

    worktree = client.create_worktree(clone, "feature-x", "main", harness_root)

    assert worktree == harness_root / "workspace" / "widgets.worktrees" / "feature-x"
    assert worktree.is_dir()


def test_create_worktree_excludes_the_workspace_folder_exactly_once_across_repeated_creations(
    tmp_path: Path,
) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    client = GitClient()

    client.create_worktree(checkout, "feature-x", "main", checkout)
    client.create_worktree(checkout, "feature-y", "main", checkout)

    exclude = (checkout / ".git" / "info" / "exclude").read_text().splitlines()
    assert exclude.count("workspace/") == 1


def test_create_worktree_modifies_no_tracked_file_of_the_checkout(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    client = GitClient()

    client.create_worktree(checkout, "feature-x", "main", checkout)

    assert git(checkout, "status", "--porcelain") == ""

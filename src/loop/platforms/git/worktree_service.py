from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

from loop.errors import CommandError
from loop.platforms.git.client import GitClient, GitRunner, Hook
from loop.platforms.git.objects import Branch, Worktree
from loop.process import checked_output, execute

_HEADS = "refs/heads/"


def _repository_root(path: Path) -> Path:
    """The checkout `path` belongs to: `path` itself, or the checkout a linked worktree's `.git` file points at."""
    git_entry = path / ".git"
    if git_entry.is_dir():
        return path
    gitdir = Path(git_entry.read_text().removeprefix("gitdir:").strip())
    return gitdir.parent.parent.parent


class WorktreeService:
    """Attaches, inspects, and removes worktrees; placement and branch naming are the caller's job."""

    def __init__(self, git: GitClient | None = None, *, run: GitRunner = execute) -> None:
        self._git = git or GitClient(run=run)
        self._execute = run

    def create(self, branch: Branch, target: Path) -> Worktree:
        """Attaches `branch` at `target`.

        Reuses `target` when it is already registered with `branch`, replaces a clean leftover there,
        and refuses a dirty leftover or a `branch` checked out at another path.
        """
        checkout = branch.path
        with self._git.lock:
            worktrees = self.list(checkout)
            existing = next((worktree for worktree in worktrees if worktree.path == target), None)
            if existing is not None and existing.branch == branch:
                return existing
            other = next(
                (worktree.path for worktree in worktrees if worktree.branch == branch and worktree.path != target),
                None,
            )
            if other is not None:
                raise CommandError("git worktree add", None, f"branch {branch.name!r} is checked out in {other}")
            if existing is not None:
                if self.has_changes(existing):
                    raise CommandError(
                        "git worktree add", None, f"leftover worktree has uncommitted changes: {target}"
                    )
                self._run(("git", "worktree", "remove", "--force", str(target)), cwd=checkout)
            self._run(("git", "worktree", "add", str(target), branch.name), cwd=checkout)
        return Worktree(target, branch)

    def list(self, checkout: Path) -> list[Worktree]:
        """Every worktree of `checkout`'s repository, with its branch, or None when its HEAD is detached."""
        output = self._run(("git", "worktree", "list", "--porcelain"), cwd=checkout)
        entries: list[Worktree] = []
        path: Path | None = None
        branch: Branch | None = None
        for line in output.splitlines():
            if line.startswith("worktree "):
                if path is not None:
                    entries.append(Worktree(path, branch))
                path = Path(line[len("worktree ") :])
                branch = None
            elif line.startswith(f"branch {_HEADS}"):
                branch = Branch(path, line[len(f"branch {_HEADS}") :])
        if path is not None:
            entries.append(Worktree(path, branch))
        return entries

    def get(self, path: Path) -> Worktree:
        """The worktree at `path`, found through its own repository, whatever checkout it belongs to."""
        checkout = _repository_root(path)
        for worktree in self.list(checkout):
            if worktree.path == path:
                return worktree
        raise CommandError("git worktree list", None, f"unknown worktree: {path}")

    def remove(self, worktree: Worktree, *, force: bool = False) -> None:
        """Removes `worktree`, found through its own repository; refuses a dirty one unless `force`."""
        checkout = _repository_root(worktree.path)
        args = (
            ("git", "worktree", "remove", "--force", str(worktree.path))
            if force
            else ("git", "worktree", "remove", str(worktree.path))
        )
        with self._git.lock:
            self._run(args, cwd=checkout)

    def detach(self, worktree: Worktree) -> Worktree:
        """Detaches `worktree`'s HEAD; it carries no branch afterwards."""
        self._run(("git", "checkout", "--detach"), cwd=worktree.path)
        return Worktree(worktree.path, None)

    def has_changes(self, worktree: Worktree) -> bool:
        """Whether `worktree` has staged, unstaged, or untracked changes."""
        return bool(self._run(("git", "status", "--porcelain"), cwd=worktree.path).strip())

    def is_clean(self, worktree: Worktree) -> bool:
        return not self.has_changes(worktree)

    def run_hook(self, hook: Hook, worktree: Path, cancel: threading.Event | None = None) -> None:
        """Runs a worktree-ready/sandbox-ready Hook on the host, inside `worktree`."""
        self._git.run_hook(hook, worktree, cancel)

    def _run(self, args: Sequence[str], *, cwd: Path) -> str:
        result = self._execute(args, cwd=cwd)
        return checked_output(" ".join(args), result)

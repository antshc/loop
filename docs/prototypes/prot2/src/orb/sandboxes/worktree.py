from __future__ import annotations

import logging
import threading
from pathlib import Path
from uuid import uuid4

from orb.contracts.sandbox import SandboxInstance, SandboxProvider
from orb.errors import CommandError
from orb.process import execute, run_command

_logger = logging.getLogger("orb")


class WorktreeSandbox(SandboxInstance):
    def __init__(
        self,
        *,
        repo: Path,
        path: Path,
        branch: str,
        host_branch: str,
        generated: bool,
        lock: threading.Lock,
    ) -> None:
        self._repo = repo
        self._path = path
        self._branch = branch
        self._host_branch = host_branch
        self._generated = generated
        self._lock = lock

    @property
    def path(self) -> Path:
        return self._path

    @property
    def branch(self) -> str:
        return self._branch

    @property
    def host_branch(self) -> str:
        return self._host_branch

    def exec(self, command: str, *, timeout_s: float | None = None) -> str:
        return run_command(command, cwd=self._path, timeout_s=timeout_s)

    def head(self) -> str:
        return run_command(("git", "rev-parse", "HEAD"), cwd=self._path).strip()

    def commits_since(self, revision: str) -> tuple[str, ...]:
        output = run_command(("git", "rev-list", "--reverse", f"{revision}..HEAD"), cwd=self._path)
        return tuple(output.split())

    def merge_into_host(self) -> None:
        with self._lock:
            try:
                run_command(("git", "merge", "--no-edit", self._branch), cwd=self._repo)
            except CommandError:
                execute(("git", "merge", "--abort"), cwd=self._repo)
                raise

    def close(self) -> None:
        tracked_changes = run_command(
            ("git", "status", "--porcelain", "--untracked-files=no"), cwd=self._path
        )
        if tracked_changes.strip():
            _logger.warning("keeping dirty worktree %s", self._path)
            return
        with self._lock:
            run_command(("git", "worktree", "remove", "--force", str(self._path)), cwd=self._repo)
            if self._generated:
                # -d refuses an unmerged branch, so unmerged work stays on its branch.
                execute(("git", "branch", "-d", self._branch), cwd=self._repo)


class WorktreeSandboxProvider(SandboxProvider):
    """Runs agents in `git worktree` checkouts on the host; no container isolation."""

    def __init__(self, *, worktrees_dir: Path | None = None) -> None:
        self._worktrees_dir = worktrees_dir
        self._lock = threading.Lock()

    def open(self, repo: Path, branch: str | None) -> SandboxInstance:
        generated = branch is None
        name = branch or f"orb/run-{uuid4().hex[:8]}"
        # Rejects option-like and malformed names, so a branch from agent output is safe to pass to git.
        run_command(("git", "check-ref-format", "--branch", name), cwd=repo)
        root = self._worktrees_dir or repo / ".orb" / "worktrees"
        path = root / name.replace("/", "__")
        with self._lock:
            host_branch = run_command(("git", "rev-parse", "--abbrev-ref", "HEAD"), cwd=repo).strip()
            if not path.exists():
                root.mkdir(parents=True, exist_ok=True)
                if self._worktrees_dir is None:
                    (repo / ".orb" / ".gitignore").write_text("*\n")
                run_command(self._add_command(repo, name, path), cwd=repo)
        return WorktreeSandbox(
            repo=repo,
            path=path,
            branch=name,
            host_branch=host_branch,
            generated=generated,
            lock=self._lock,
        )

    @staticmethod
    def _add_command(repo: Path, branch: str, path: Path) -> tuple[str, ...]:
        if execute(("git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"), cwd=repo).returncode == 0:
            return ("git", "worktree", "add", str(path), branch)
        remote = f"refs/remotes/origin/{branch}"
        if execute(("git", "show-ref", "--verify", "--quiet", remote), cwd=repo).returncode == 0:
            return ("git", "worktree", "add", "--track", "-b", branch, str(path), f"origin/{branch}")
        return ("git", "worktree", "add", "-b", branch, str(path), "HEAD")


def worktree(*, worktrees_dir: Path | None = None) -> WorktreeSandboxProvider:
    return WorktreeSandboxProvider(worktrees_dir=worktrees_dir)

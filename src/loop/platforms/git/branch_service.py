from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from loop.platforms.git.client import GitClient, GitRunner
from loop.platforms.git.objects import Branch
from loop.process import checked_output, execute


class BranchService:
    """Prepares, publishes, and retires branches; hides local/remote ref presence and start-ref selection."""

    def __init__(self, git: GitClient, *, run: GitRunner = execute) -> None:
        self._git = git
        self._execute = run

    def fetch(self, checkout: Path) -> None:
        """Fetches and prunes every remote of `checkout`."""
        self._run(("git", "fetch", "--all", "--prune"), cwd=checkout)

    def can_prepare(self, base: Branch) -> bool:
        """Whether `base` has an `origin` counterpart a branch can be prepared from."""
        return self._git.remote_branch_exists(base.path, base.name)

    def prepare(self, branch: Branch | None, base: Branch) -> Branch:
        """Ensures a local branch exists at the right start ref; generates a name when `branch` is None."""
        checkout = base.path
        name = branch.name if branch is not None else self._generate_name()
        self._git.check_branch_name(checkout, name)
        wanted = Branch(checkout, name)
        start_ref = wanted.upstream if self._git.remote_branch_exists(checkout, name) else base.upstream
        with self._git.lock:
            if self._exists_local(checkout, name):
                self._run(("git", "branch", "-f", name, start_ref), cwd=checkout)
            else:
                self._run(("git", "branch", name, start_ref), cwd=checkout)
        return wanted

    def ahead_of_remote(self, branch: Branch, base: Branch) -> bool:
        """Whether local `branch` holds commits beyond its `origin` counterpart, or beyond `base` when it has none."""
        if not self._exists_local(branch.path, branch.name):
            return False
        upstream = branch.upstream if self._git.remote_branch_exists(branch.path, branch.name) else base.upstream
        return bool(self._run(("git", "rev-list", f"{upstream}..{branch.name}"), cwd=branch.path).strip())

    def push(self, branch: Branch) -> None:
        """Pushes `branch` to `origin`."""
        self._run(("git", "push", "origin", branch.name), cwd=branch.path)

    def merge(self, target: Path, branch: Branch) -> None:
        """Merges `branch` into `target`'s checked-out branch."""
        with self._git.lock:
            self._run(("git", "merge", "--no-edit", branch.name), cwd=target)

    def delete(self, branch: Branch) -> None:
        """Deletes the local `branch`."""
        with self._git.lock:
            self._run(("git", "branch", "-D", branch.name), cwd=branch.path)

    @staticmethod
    def _generate_name() -> str:
        return f"loop/sandbox-{uuid4().hex[:8]}"

    def _exists_local(self, checkout: Path, name: str) -> bool:
        return self._execute(("git", "show-ref", "--verify", "--quiet", Branch(checkout, name).ref), cwd=checkout).returncode == 0

    def _run(self, args: Sequence[str], *, cwd: Path) -> str:
        result = self._execute(args, cwd=cwd)
        return checked_output(" ".join(args), result)

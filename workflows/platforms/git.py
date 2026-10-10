"""Git reads and publication the workflows need beyond the loop library's worktree lifecycle."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .process import CommandError, run_command

Run = Callable[..., str]

# Unit/record separators keep a commit's fields apart even when its body carries newlines.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"
_LOG_FORMAT = f"%H{_FIELD_SEP}%s{_RECORD_SEP}"


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str


class WorkflowGit:
    """Git operations on a given checkout or worktree; `run` is the injectable process seam."""

    def __init__(self, *, run: Run = run_command) -> None:
        self._run = run

    def fetch(self, path: Path) -> None:
        self._git(path, "fetch", "--all", "--prune")

    def remote_branch_exists(self, path: Path, branch: str) -> bool:
        return self._succeeds(path, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}")

    def push_if_ahead(self, path: Path, branch: str, base: str) -> bool:
        """Pushes `branch` when it holds commits beyond its `origin` counterpart (or beyond `base` when it has none); returns whether it pushed."""
        if not self._succeeds(path, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
            return False
        upstream = f"origin/{branch}" if self.remote_branch_exists(path, branch) else f"origin/{base}"
        if not self._git(path, "rev-list", f"{upstream}..{branch}").strip():
            return False
        self._git(path, "push", "origin", branch)
        return True

    def head(self, path: Path) -> Commit:
        return self._log(path, "-1")[0]

    def commits_since(self, path: Path, sha: str) -> list[Commit]:
        """Commits after `sha` up to HEAD, oldest first."""
        return self._log(path, "--reverse", f"{sha}..HEAD")

    def initiative_commits(self, path: Path, base: str, prefix: str) -> list[Commit]:
        """Commits after `origin/<base>` whose subject starts with `prefix`, oldest first."""
        commits = self._log(path, "--reverse", "--fixed-strings", f"--grep={prefix}", f"origin/{base}..HEAD")
        # `--grep` also matches body lines, so keep only subjects that carry the prefix.
        return [commit for commit in commits if commit.subject.startswith(prefix)]

    def restore(self, path: Path, sha: str) -> None:
        """Hard-resets `path` to `sha` and removes untracked files and directories."""
        self._git(path, "reset", "--hard", sha)
        self._git(path, "clean", "-fd")

    def is_clean(self, path: Path) -> bool:
        return not self._git(path, "status", "--porcelain").strip()

    def _log(self, path: Path, *args: str) -> list[Commit]:
        output = self._git(path, "log", f"--format={_LOG_FORMAT}", *args)
        records = (record.lstrip("\n") for record in output.split(_RECORD_SEP))
        return [Commit(*record.split(_FIELD_SEP)) for record in records if record]

    def _succeeds(self, path: Path, *args: str) -> bool:
        try:
            self._git(path, *args)
        except CommandError:
            return False
        return True

    def _git(self, path: Path, *args: str) -> str:
        command: Sequence[str] = ("git", *args)
        return self._run(command, cwd=path)

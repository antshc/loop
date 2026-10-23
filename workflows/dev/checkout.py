"""Git reads and publication for the dev Workflow."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from .commands import CommandError, run_command
from .records import Commit


Run = Callable[..., str]


_FIELD_SEP = "\x1f"


_RECORD_SEP = "\x1e"


_LOG_FORMAT = f"%H{_FIELD_SEP}%s{_FIELD_SEP}%b{_RECORD_SEP}"


class GitService:
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

    def branch_commits(self, path: Path, base: str) -> list[Commit]:
        """Commits after `origin/<base>` up to HEAD, oldest first, with their bodies."""
        return self._log(path, "--reverse", f"origin/{base}..HEAD")

    def initiative_commits(self, path: Path, base: str, prefix: str) -> list[Commit]:
        """Commits after `origin/<base>` whose subject starts with `prefix`, oldest first."""
        commits = self._log(path, "--reverse", "--fixed-strings", f"--grep={prefix}", f"origin/{base}..HEAD")
        # `--grep` also matches body lines, so keep only subjects that carry the prefix.
        return [commit for commit in commits if commit.subject.startswith(prefix)]

    def commit(self, path: Path, subject: str, body: str = "") -> Commit:
        """Stages every change in `path`, commits it and returns the new HEAD; used for verifier and cleanup edits."""
        self._git(path, "add", "-A")
        message = ["commit", "-m", subject, *(["-m", body] if body.strip() else [])]
        self._git(path, *message)
        return self.head(path)

    def changed_files(self, path: Path) -> list[str]:
        """Paths with uncommitted changes (renames report the new path), untracked files included."""
        files: list[str] = []
        for line in self._git(path, "status", "--porcelain", "--untracked-files=all").splitlines():
            entry = line[3:].split(" -> ")[-1].strip().strip('"')
            if entry:
                files.append(entry)
        return files

    def status(self, path: Path) -> str:
        """The raw porcelain status, for comparing a checkout before and after a read-only run."""
        return self._git(path, "status", "--porcelain", "--untracked-files=all")

    def ref_sha(self, path: Path, ref: str) -> str:
        return self._git(path, "rev-parse", ref).strip()

    def restore(self, path: Path, sha: str) -> None:
        """Hard-resets `path` to `sha` and removes untracked files and directories."""
        self._git(path, "reset", "--hard", sha)
        self._git(path, "clean", "-fd")

    def is_clean(self, path: Path) -> bool:
        return not self._git(path, "status", "--porcelain").strip()

    def _log(self, path: Path, *args: str) -> list[Commit]:
        output = self._git(path, "log", f"--format={_LOG_FORMAT}", *args)
        records = (record.lstrip("\n") for record in output.split(_RECORD_SEP))
        fields = (record.split(_FIELD_SEP, 2) for record in records if record)
        return [Commit(sha, subject, rest[0].strip() if rest else "") for sha, subject, *rest in fields]

    def _succeeds(self, path: Path, *args: str) -> bool:
        try:
            self._git(path, *args)
        except CommandError:
            return False
        return True

    def _git(self, path: Path, *args: str) -> str:
        command: Sequence[str] = ("git", *args)
        return self._run(command, cwd=path)

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from loop.platforms.git.client import GitRunner
from loop.platforms.git.objects import Branch, Commit
from loop.process import checked_output, execute

# `%x1f`/`%x1e` (unit/record separator) keep a commit's subject and body apart from the next commit,
# even when the body itself carries newlines.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"
_LOG_FORMAT = f"%H{_FIELD_SEP}%s{_FIELD_SEP}%b{_RECORD_SEP}"


class CommitService:
    """Reads and rolls back commits as `Commit` objects; hides HEAD, log formatting, revision ranges, and reset/clean."""

    def __init__(self, *, run: GitRunner = execute) -> None:
        self._execute = run

    def head(self, path: Path) -> Commit:
        """The commit at `path`'s HEAD, with its full hash, subject, and body."""
        return self._log(path, ("git", "log", "-1", f"--format={_LOG_FORMAT}"))[0]

    def since(self, commit: Commit) -> list[Commit]:
        """Every commit made after `commit` up to HEAD in its `path`, oldest first, merges included."""
        return self._log(commit.path, ("git", "log", "--reverse", f"--format={_LOG_FORMAT}", f"{commit.sha}..HEAD"))

    def find_since(self, base: Branch, *, subject_prefix: str) -> list[Commit]:
        """Commits after `base`'s `origin` counterpart whose subject starts with `subject_prefix`, oldest first."""
        commits = self._log(
            base.path,
            (
                "git",
                "log",
                "--reverse",
                "--fixed-strings",
                f"--grep={subject_prefix}",
                f"--format={_LOG_FORMAT}",
                f"{base.upstream}..HEAD",
            ),
        )
        # `--grep` also matches body lines, so keep only subjects that carry the prefix.
        return [commit for commit in commits if commit.subject.startswith(subject_prefix)]

    def restore(self, commit: Commit) -> None:
        """Hard-resets `commit`'s `path` to it and removes untracked files and directories."""
        self._run(("git", "reset", "--hard", commit.sha), cwd=commit.path)
        self._run(("git", "clean", "-fd"), cwd=commit.path)

    def identity(self, path: Path) -> tuple[str, str]:
        """The `user.name` and `user.email` git identity configured for the repository at `path`."""
        return self._config_get(path, "user.name") or "", self._config_get(path, "user.email") or ""

    def _log(self, path: Path, args: Sequence[str]) -> list[Commit]:
        output = self._run(args, cwd=path)
        return [self._parse(path, record) for record in output.split(_RECORD_SEP) if record.strip("\n")]

    @staticmethod
    def _parse(path: Path, record: str) -> Commit:
        sha, subject, body = record.lstrip("\n").split(_FIELD_SEP)
        return Commit(path, sha, subject, body.rstrip("\n"))

    def _config_get(self, path: Path, key: str) -> str | None:
        result = self._execute(("git", "config", "--get", key), cwd=path)
        return result.stdout.strip() or None if result.returncode == 0 else None

    def _run(self, args: Sequence[str], *, cwd: Path) -> str:
        result = self._execute(args, cwd=cwd)
        return checked_output(" ".join(args), result)

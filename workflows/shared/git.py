"""Workflow-owned Git reads, commits, and non-forced publication on a checkout or worktree."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .process import CommandError, Runner


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str = ""


_FIELD_SEP = "\x1f"


_RECORD_SEP = "\x1e"


_LOG_FORMAT = f"%H{_FIELD_SEP}%s{_FIELD_SEP}%b{_RECORD_SEP}"


_GITHUB_REMOTE = re.compile(r"^(git@github\.com:|https://github\.com/)")


def github_slug(remote: str) -> str:
    """`owner/name` of a GitHub remote URL; other URLs come back unchanged apart from a `.git` suffix."""
    return re.sub(r"\.git$", "", _GITHUB_REMOTE.sub("", remote.strip()))


class GitClient:
    """Git on a given path through the injected process runner; never forces a push."""

    def __init__(self, run: Runner) -> None:
        self._run = run

    def text(self, path: Path, *args: str) -> str:
        """Runs `git <args>` in `path` and returns its stripped stdout."""
        return self._git(path, *args).strip()

    def origin_slug(self, path: Path) -> str:
        """`owner/name` of the `origin` remote."""
        return github_slug(self.text(path, "remote", "get-url", "origin"))

    def fetch(self, path: Path) -> None:
        self._git(path, "fetch", "--all", "--prune")

    def fetch_revision(self, path: Path, sha: str) -> None:
        """Fetches one expected revision without changing the checkout."""
        self._git(path, "fetch", "origin", sha)

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

    def publish(self, path: Path, branch: str) -> None:
        """Pushes HEAD to `branch` on `origin` without force."""
        self._git(path, "check-ref-format", f"refs/heads/{branch}")
        self._git(path, "push", "origin", f"HEAD:refs/heads/{branch}")

    def advance(self, path: Path, sha: str) -> None:
        """Fast-forwards the checked-out branch to `sha`."""
        self._git(path, "merge", "--ff-only", sha)

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
        """Stages every change in `path`, commits it and returns the new HEAD."""
        self._git(path, "add", "-A")
        self._git(path, "commit", "-m", subject, *(["-m", body] if body.strip() else []))
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
        """Whether `path` has no staged, unstaged, or untracked change."""
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
        return self._run(("git", *args), cwd=path)

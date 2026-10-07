from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_HEADS = "refs/heads/"


@dataclass(frozen=True)
class Branch:
    """A local branch and its `origin` counterpart; equality is by name, whatever `path` the branch is seen from."""

    path: Path = field(compare=False)
    name: str

    @property
    def ref(self) -> str:
        return f"{_HEADS}{self.name}"

    @property
    def remote_ref(self) -> str:
        return f"refs/remotes/origin/{self.name}"

    @property
    def upstream(self) -> str:
        return f"origin/{self.name}"


@dataclass(frozen=True)
class Worktree:
    """A worktree of a Codebase Checkout; `branch` is None on a detached HEAD."""

    path: Path
    branch: Branch | None = None


@dataclass(frozen=True)
class Commit:
    """A commit in the repository at `path`, identified by its (possibly short) hash, subject, and body."""

    path: Path
    sha: str
    subject: str
    body: str = ""

    @property
    def message(self) -> str:
        return self.subject if not self.body else f"{self.subject}\n\n{self.body}"

    @classmethod
    def parse(cls, path: Path, line: str) -> Commit:
        """A Commit from a `%h %s` log line."""
        sha, _, subject = line.partition(" ")
        return cls(path, sha, subject)

    @staticmethod
    def subject_prefix(identifier: str) -> str:
        """The required prefix of a Ticket's delivering commit subject."""
        return f"ccode({identifier}): "

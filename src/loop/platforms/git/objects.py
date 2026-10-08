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
class RepositoryData:
    """A repository's resolved location: its checkout path, `owner/name`, harness flag, and worktree root."""

    path: Path
    owner_repo: str
    is_harness: bool
    worktree_root: Path


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

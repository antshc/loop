"""Outside systems middlewares read from."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Issue:
    """A GitHub issue as the ticket middleware renders it."""

    repo: str
    number: int
    title: str
    body: str


class GitHubClient(Protocol):
    """Reads GitHub issues."""

    async def get_issue(self, repo: str, number: int) -> Issue: ...


class TicketCache:
    """SESSION-scoped: every call of one run reads each issue once."""

    def __init__(self, github: GitHubClient) -> None:
        self._github = github
        self._issues: dict[tuple[str, int], Issue] = {}

    async def get_issue(self, repo: str, number: int) -> Issue:
        """The cached issue, fetched on first use."""
        key = (repo, number)
        if key not in self._issues:
            self._issues[key] = await self._github.get_issue(repo, number)
        return self._issues[key]


@dataclass(frozen=True)
class RetryPolicy:
    """How many times a mismatched reply is re-asked."""

    max_attempts: int = 3


class PlanStore:
    """SESSION-scoped: the planner's reply, kept for the implementer of the same run."""

    def __init__(self) -> None:
        self.plan: str | None = None


@dataclass(frozen=True)
class CheckResult:
    """The outcome of the verification commands."""

    passed: bool
    output: str


class CheckRunner(Protocol):
    """Runs the repository's verification commands in the agent's worktree."""

    async def run(self) -> CheckResult: ...


class NoChecks:
    """The default when a Workflow configures no checks; always passes."""

    async def run(self) -> CheckResult:
        """A passing result with no output."""
        return CheckResult(True, "")


@dataclass(frozen=True)
class ReviewChecklist:
    """Checklist items only the reviewer role receives."""

    items: tuple[str, ...]


@dataclass(frozen=True)
class Commit:
    """One commit on a worktree's branch."""

    sha: str
    subject: str


class WorktreeGit(Protocol):
    """Git evidence and rollback for the worktree an agent runs in."""

    async def head(self, path: str) -> str: ...

    async def status(self, path: str) -> str: ...

    async def commits_since(self, path: str, sha: str) -> list[Commit]: ...

    async def branch_commits(self, path: str) -> list[Commit]: ...

    async def commit(self, path: str, message: str) -> str: ...

    async def restore(self, path: str, sha: str) -> None: ...

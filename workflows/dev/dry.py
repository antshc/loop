"""Dry-run slice of the dev Workflow: a scripted fake agent over in-memory Git and GitHub, so a whole run needs no CLI, repository or network."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
import tempfile

from loop import (
    AgentBuilder,
    AgentRequest,
    FakeCliRunner,
    FakeReply,
    FakeToolUse,
    GitCli,
    GitRuntime,
    RunContext,
    SessionLogReader,
    Usage,
    fake_agent,
)

from .checkout import GitService
from .commands import run_command
from .journal import FileExecutionStore
from .platforms import GitHubRepo
from .records import Commit, RepositoryId


logger = logging.getLogger("workflow.dev")


DRY_TARGET = RepositoryId("antshc", "loop")


DRY_BASE_BRANCH = "main"


_SPEC_NUMBER = 1


_TICKET_TITLES = ("Add discount policy", "Apply discount at checkout")


_TICKET_NUMBERS = tuple(range(10, 10 + len(_TICKET_TITLES)))


_SUBJECT = re.compile(r"subject `(?P<prefix>ccode\((?P<identifier>[^)|]+\|(?P<number>\d+))\): )")


_TICKET_JSON = re.compile(r"<ticket-json>\n(?P<json>.*?)\n</ticket-json>", re.DOTALL)


class DryGit(GitService):
    """An in-memory history on one branch: a commit list and how much of it `origin` already holds."""

    def __init__(self) -> None:
        self.commits = [Commit("base0000", "base")]
        self._pushed = 1

    def commit(self, subject: str) -> str:
        """Adds a commit and returns its SHA."""
        sha = f"c{len(self.commits):07d}"
        self.commits.append(Commit(sha, subject))
        return sha

    def fetch(self, path: Path) -> None:
        logger.info("dry-run: git fetch %s", path)

    def remote_branch_exists(self, path: Path, branch: str) -> bool:
        return True

    def push_if_ahead(self, path: Path, branch: str, base: str) -> bool:
        if len(self.commits) <= self._pushed:
            return False
        logger.info("dry-run: git push origin %s", branch)
        self._pushed = len(self.commits)
        return True

    def head(self, path: Path) -> Commit:
        return self.commits[-1]

    def commits_since(self, path: Path, sha: str) -> list[Commit]:
        return self.commits[self._index(sha) + 1 :]

    def initiative_commits(self, path: Path, base: str, prefix: str) -> list[Commit]:
        return [commit for commit in self.commits[1:] if commit.subject.startswith(prefix)]

    def restore(self, path: Path, sha: str) -> None:
        del self.commits[self._index(sha) + 1 :]

    def is_clean(self, path: Path) -> bool:
        return True

    def _index(self, sha: str) -> int:
        return next(index for index, commit in enumerate(self.commits) if commit.sha == sha)


def _issue(number: int, title: str, labels: tuple[str, ...]) -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/{DRY_TARGET}/issues/{number}",
        "state": "OPEN",
        "body": f"Dry-run body of #{number}: {title}.",
        "labels": {"nodes": [{"name": label} for label in labels]},
        "comments": {"nodes": []},
    }


class DryGh:
    """Stands in for `gh`: canned reads for one Spec with two Tickets; writes are logged, not sent."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        labels = ("spec", f"repo:target:{DRY_TARGET}", f"repo:base:{DRY_BASE_BRANCH}")
        self._specs = [_issue(_SPEC_NUMBER, "Basket: Discount policy", labels)]
        self._tickets = [_issue(number, title, ()) for number, title in zip(_TICKET_NUMBERS, _TICKET_TITLES)]

    def __call__(self, args: tuple[str, ...]) -> str:
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return "[]"
        if args[:2] == ("pr", "create"):
            return f"https://github.com/{DRY_TARGET}/pull/99"
        if args[0] == "issue":
            logger.info("dry-run: gh %s", " ".join(args[:3]))
            return ""
        query = next((arg for arg in args if arg.startswith("query=")), "")
        if "subIssues" in query:
            return self._page({"issue": {"subIssues": {"nodes": self._tickets}}})
        return self._page({"issues": {"nodes": self._specs}})

    @staticmethod
    def _page(repository: dict) -> str:
        return json.dumps([{"data": {"repository": repository}}])


Committer = Callable[[Path, str], str]


def commit_in_worktree(cwd: Path, subject: str) -> str:
    """Makes a real empty commit in `cwd` and returns its SHA; for a fake agent over real Git."""
    run_command(("git", "commit", "--allow-empty", "-m", subject), cwd=cwd)
    return run_command(("git", "rev-parse", "HEAD"), cwd=cwd).strip()


@dataclass
class ScriptedAgent:
    """Answers each Ticket with one commit made by `commit`; the second Ticket's first attempt fails when `rework`."""

    commit: Committer
    rework: bool = True
    _attempts: dict[str, int] = field(default_factory=dict, init=False)

    def __call__(self, request: AgentRequest, context: RunContext) -> FakeReply:
        subject = _SUBJECT.search(request.prompt)
        if subject is None:
            return FakeReply("dry-run: no commit subject in the prompt")
        identifier = subject["identifier"]
        attempt = self._attempts[identifier] = self._attempts.get(identifier, 0) + 1
        title = self._title(request.prompt)
        if self.rework and int(subject["number"]) == _TICKET_NUMBERS[1] and attempt == 1:
            return self._failed(identifier, f"dry-run: tests for {title!r} failed")
        return self._completed(identifier, subject["prefix"], title, int(subject["number"]), context.agent.cwd)

    @staticmethod
    def _title(prompt: str) -> str:
        match = _TICKET_JSON.search(prompt)
        return json.loads(match["json"])["title"] if match else "ticket"

    def _completed(self, identifier: str, prefix: str, title: str, number: int, cwd: Path) -> FakeReply:
        sha = self.commit(cwd, f"{prefix}{title}")
        result = {"commit": sha, "summary": title, "verification": "dry-run: dotnet test -> 12 passed"}
        return FakeReply(
            _answer(identifier, "completed", result, f"Implemented {title}."),
            usage=_usage(number),
            tools=(
                FakeToolUse("view", {"path": "src/Basket.cs"}, "public class Basket { }", seconds=0.3),
                FakeToolUse("edit", {"path": "src/Discount.cs"}, "updated", seconds=0.6),
                FakeToolUse("bash", {"command": "dotnet test"}, "12 passed", seconds=1.5),
            ),
        )

    @staticmethod
    def _failed(identifier: str, reason: str) -> FakeReply:
        return FakeReply(
            _answer(identifier, "failed", {"reason": reason}, "The checks failed."),
            usage=_usage(1),
            tools=(FakeToolUse("bash", {"command": "dotnet test"}, "1 failed", ok=False, seconds=1.2),),
        )


def _answer(identifier: str, status: str, result: dict, narration: str) -> str:
    envelope = json.dumps({"identifier": identifier, "status": status, "result": result})
    return f"{narration}\n\n```json\n{envelope}\n```"


def _usage(seed: int) -> Usage:
    return Usage(
        api_calls=3 + seed % 3,
        input_tokens=4_000 + 500 * seed,
        output_tokens=900 + 100 * seed,
        cache_read_tokens=12_000,
        aiu={"input": 0.4 + 0.05 * seed, "output": 0.9 + 0.1 * seed},
        premium_requests=0.33,
    )


SEAMS = ("agent", "git", "github", "store")


class Fakes:
    """Replaces any subset of the dev Workflow's external dependencies with fakes; every other seam stays real.

    agent: the Copilot CLI, scripted. git: the Workflow's Git reads and pushes plus the agent's worktree, in memory.
    github: `gh`, canned. store: the failure log, in a temporary directory.
    """

    def __init__(self, seams: Collection[str], *, speed: float = 1.0, rework: bool = True) -> None:
        unknown = sorted(set(seams) - set(SEAMS))
        if unknown:
            raise ValueError(f"unknown seam {', '.join(unknown)}; choose from {', '.join(SEAMS)}")
        if "git" in seams and "agent" not in seams:
            raise ValueError("a faked git needs a faked agent: a real agent would commit where the fake git cannot see")
        self.seams = frozenset(seams)
        self._git = DryGit()
        self._gh = DryGh()
        commit: Committer = (lambda cwd, subject: self._git.commit(subject)) if "git" in self.seams else commit_in_worktree
        self._runner = FakeCliRunner(ScriptedAgent(commit, rework), speed=speed)

    def git(self) -> GitService | None:
        """The in-memory Git when faked, else None."""
        return self._git if "git" in self.seams else None

    def github_factory(self) -> Callable[[Path], GitHubRepo] | None:
        """A factory of the canned GitHub repository when faked, else None."""
        return (lambda checkout: GitHubRepo(DRY_TARGET.owner, DRY_TARGET.repo, gh=self._gh)) if "github" in self.seams else None

    def new_agent(self) -> Callable[[], AgentBuilder] | None:
        """Builders over the scripted agent when faked, else None; the worktree is a temporary directory only with a faked git."""
        if "agent" not in self.seams:
            return None
        worktrees = None if "git" in self.seams else GitRuntime(GitCli())
        return lambda: fake_agent(self._runner, worktrees)

    def readers(self) -> Sequence[SessionLogReader] | None:
        """The scripted agent as the session-log reader when faked, else None."""
        return (self._runner,) if "agent" in self.seams else None

    def store(self) -> FileExecutionStore | None:
        """A failure log in a fresh temporary directory when faked, else None."""
        return FileExecutionStore(Path(tempfile.mkdtemp(prefix="loop-fake-log-"))) if "store" in self.seams else None

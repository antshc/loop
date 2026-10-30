"""Dry-run seams of the implement_spec Workflow: a scripted fake agent over in-memory Git and GitHub, so a whole run needs no CLI, repository or network."""

from __future__ import annotations

import json
import logging
import re
import tempfile
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from loop import (
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

from software_factory.runtime import WorktreeCommit, WorktreeGit
from software_factory.shared.agents.launcher import AgentFactory
from software_factory.shared.git import Commit, GitClient
from software_factory.shared.github.identity import RepositoryId
from software_factory.shared.github.repo import GitHubRepo
from software_factory.shared.process import CommandRunner

logger = logging.getLogger("workflow.implement_spec")

DRY_TARGET = RepositoryId("antshc", "loop")

DRY_BASE_BRANCH = "main"

SEAMS = ("agent", "git", "github", "store")

_SPEC_NUMBER = 1

_TICKET_TITLES = ("Add discount policy", "Apply discount at checkout")

_TICKET_NUMBERS = tuple(range(10, 10 + len(_TICKET_TITLES)))

_SUBJECT = re.compile(r"subject `(?P<prefix>ccode\((?P<identifier>[^)|]+\|(?P<number>\d+))\): )")

_TICKET_JSON = re.compile(r"<ticket-json>\n(?P<json>.*?)\n</ticket-json>", re.DOTALL)


class DryHistory:
    """One branch as a commit list and how much of it `origin` already holds; the Workflow's Git and the runtime's Git both read it."""

    def __init__(self) -> None:
        self.commits = [Commit("base0000", "base")]
        self.pushed = 1

    def commit(self, subject: str) -> str:
        """Adds a commit and returns its SHA."""
        sha = f"c{len(self.commits):07d}"
        self.commits.append(Commit(sha, subject))
        return sha

    def index(self, sha: str) -> int:
        """The position of `sha` in the history."""
        return next(index for index, commit in enumerate(self.commits) if commit.sha == sha)

    def restore(self, sha: str) -> None:
        """Drops every commit after `sha`."""
        del self.commits[self.index(sha) + 1 :]


class DryGit(GitClient):
    """The Workflow's Git over a `DryHistory`: nothing touches a repository."""

    def __init__(self, history: DryHistory) -> None:
        super().__init__(CommandRunner())
        self._history = history

    def fetch(self, path: Path) -> None:
        logger.debug("dry-run: git fetch %s", path)

    def remote_branch_exists(self, path: Path, branch: str) -> bool:
        return branch == DRY_BASE_BRANCH or self._history.pushed > 1

    def is_ahead(self, path: Path, branch: str, base: str) -> bool:
        return len(self._history.commits) > self._history.pushed

    def push_if_ahead(self, path: Path, branch: str, base: str) -> bool:
        if not self.is_ahead(path, branch, base):
            return False
        logger.debug("dry-run: git push origin %s", branch)
        self._history.pushed = len(self._history.commits)
        return True

    def head(self, path: Path) -> Commit:
        return self._history.commits[-1]

    def commits_since(self, path: Path, sha: str) -> list[Commit]:
        return self._history.commits[self._history.index(sha) + 1 :]

    def branch_commits(self, path: Path, base: str) -> list[Commit]:
        return self._history.commits[1:]

    def initiative_commits(self, path: Path, base: str, prefix: str) -> list[Commit]:
        return [commit for commit in self._history.commits[1:] if commit.subject.startswith(prefix)]

    def status(self, path: Path) -> str:
        return ""

    def changed_files(self, path: Path) -> list[str]:
        return []

    def ref_sha(self, path: Path, ref: str) -> str:
        return self._history.commits[self._history.pushed - 1].sha

    def restore(self, path: Path, sha: str) -> None:
        self._history.restore(sha)

    def is_clean(self, path: Path) -> bool:
        return True


class DryWorktreeGit(WorktreeGit):
    """The runtime's Git evidence, commits and restores over a `DryHistory`."""

    def __init__(self, history: DryHistory) -> None:
        super().__init__(self._refuse)
        self._history = history

    @staticmethod
    def _refuse(args: Sequence[str], path: Path, *, env: object = None) -> str:
        raise RuntimeError(f"dry-run: git {' '.join(args)} has no fake")

    def head(self, path: Path) -> str:
        return self._history.commits[-1].sha

    def status(self, path: Path) -> str:
        return ""

    def is_clean(self, path: Path) -> bool:
        return True

    def parents(self, path: Path) -> tuple[str, ...]:
        return (self._history.commits[-2].sha,) if len(self._history.commits) > 1 else ()

    def is_ancestor(self, path: Path, sha: str) -> bool:
        return any(commit.sha == sha for commit in self._history.commits)

    def tree_id(self, path: Path) -> str:
        return f"tree-{self._history.commits[-1].sha}"

    def commits_since(self, path: Path, sha: str) -> tuple[WorktreeCommit, ...]:
        return tuple(WorktreeCommit(commit.sha, commit.subject) for commit in self._history.commits[self._history.index(sha) + 1 :])

    def commit(self, path: Path, message: str) -> str:
        return self._history.commit(message.splitlines()[0])

    def restore(self, path: Path, sha: str) -> None:
        self._history.restore(sha)


def _issue(number: int, title: str, labels: tuple[str, ...], *, sub_issues: int = 0) -> dict:
    return {
        "id": 1000 + number,
        "node_id": f"I_dry{number}",
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{DRY_TARGET}/issues/{number}",
        "state": "open",
        "state_reason": None,
        "body": f"Dry-run body of #{number}: {title}.",
        "labels": [{"name": label} for label in labels],
        "assignees": [],
        "comments": 0,
        "sub_issues_summary": {"total": sub_issues},
    }


class DryGh:
    """Stands in for `gh`: one Spec with two Tickets and its pull requests, whose writes are applied to its own state."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        labels = ("spec", f"repo:target:{DRY_TARGET}", f"repo:base:{DRY_BASE_BRANCH}")
        self._tickets = [_issue(number, title, ()) for number, title in zip(_TICKET_NUMBERS, _TICKET_TITLES)]
        self._specs = [_issue(_SPEC_NUMBER, "Basket: Discount policy", labels, sub_issues=len(self._tickets))]
        self._comments: dict[int, list[dict]] = {}
        self._pulls: list[dict] = []
        self._creations = 0

    def __call__(self, args: tuple[str, ...]) -> str:
        self.calls.append(args)
        if args[0] == "pr":
            return self._pull_request(args)
        if args[0] == "issue":
            return self._issue_write(args)
        endpoint = next(arg for arg in args if arg.startswith("repos/"))
        if "--method" in args:
            return self._post(endpoint, args)
        path, _, _ = endpoint.partition("?")
        if "--paginate" in args:
            return json.dumps([self._collection(path)])
        return json.dumps(self._issue(int(path.rsplit("/", 1)[1])))

    def _pull_request(self, args: tuple[str, ...]) -> str:
        match args[1]:
            case "list":
                head = args[args.index("--head") + 1]
                return json.dumps([pull for pull in self._pulls if pull["headRefName"] == head])
            case "create":
                self._creations += 1
                pull = {
                    "number": 99, "title": args[args.index("--title") + 1], "url": f"https://github.com/{DRY_TARGET}/pull/99",
                    "headRefName": args[args.index("--head") + 1], "body": args[args.index("--body") + 1], "isDraft": True,
                }
                # The first pull request is not listed yet, as the earlier canned `gh` never listed one: the recorded run keeps its second `pr create`.
                if self._creations > 1:
                    self._pulls = [pull]
                return pull["url"]
            case "edit":
                self._pulls[0]["body"] = args[args.index("--body") + 1]
            case "ready":
                self._pulls[0]["isDraft"] = False
        return ""

    def _issue_write(self, args: tuple[str, ...]) -> str:
        logger.debug("dry-run: gh %s", " ".join(args[:3]))
        node = self._issue(int(args[2]))
        if args[1] == "close":
            node["state"], node["state_reason"] = "closed", "completed"
        elif "--add-label" in args:
            node["labels"].append({"name": args[args.index("--add-label") + 1]})
        return ""

    def _post(self, endpoint: str, args: tuple[str, ...]) -> str:
        if not endpoint.endswith("/comments"):
            return "{}"
        number = int(endpoint.split("/")[-2])
        posted = self._comments.setdefault(number, [])
        body = next(arg.removeprefix("body=") for arg in args if arg.startswith("body="))
        comment = {"id": 5000 + number + len(posted), "user": {"login": "dry-run"}, "body": body, "created_at": "2026-01-01T00:00:00Z"}
        posted.append(comment)
        self._issue(number)["comments"] = len(posted)
        return json.dumps(comment)

    def _collection(self, path: str) -> list[dict]:
        if path.endswith("/sub_issues"):
            return self._tickets
        if path.endswith("/labels"):
            return self._issue(int(path.split("/")[-2]))["labels"]
        if path.endswith("/comments"):
            return self._comments.get(int(path.split("/")[-2]), [])
        if path.endswith("/dependencies/blocked_by"):
            return []
        return self._specs

    def _issue(self, number: int) -> dict:
        return next(node for node in (*self._specs, *self._tickets) if node["number"] == number)


type Committer = Callable[[Path, str], str]


def commit_in_worktree(cwd: Path, subject: str) -> str:
    """Makes a real empty commit in `cwd` and returns its SHA; for a fake agent over real Git."""
    run = CommandRunner()
    run(("git", "commit", "--allow-empty", "-m", subject), cwd=cwd)
    return run(("git", "rev-parse", "HEAD"), cwd=cwd).strip()


@dataclass
class ScriptedAgent:
    """Answers each Ticket with one commit made by `commit`; the second Ticket's first attempt fails when `rework`."""

    commit: Committer
    rework: bool = True
    _attempts: dict[str, int] = field(default_factory=dict, init=False)

    def __call__(self, request: AgentRequest, context: RunContext) -> FakeReply:
        subject = _SUBJECT.search(request.prompt)
        if subject is None:
            return self._other(request)
        identifier = subject["identifier"]
        attempt = self._attempts[identifier] = self._attempts.get(identifier, 0) + 1
        title = self._title(request.prompt)
        if self.rework and int(subject["number"]) == _TICKET_NUMBERS[1] and attempt == 1:
            return self._failed(identifier, f"dry-run: tests for {title!r} failed")
        return self._completed(identifier, subject["prefix"], title, int(subject["number"]), context.agent.cwd)

    @staticmethod
    def _other(request: AgentRequest) -> FakeReply:
        if request.prompt.startswith("# TASK"):
            result = {"summary": "No cleanup needed", "out_of_scope": []}
        elif request.prompt.startswith("# Review"):
            match = re.search(r"head is commit\s+`([^`]+)`", request.prompt)
            result = {
                "axes": [{"name": name, "outcome": "completed"} for name in ("bugs", "quality", "smells", "initiative")],
                "head": match[1] if match else "",
                "findings": [],
            }
        else:
            return FakeReply(json.dumps({"status": "failed", "result": {"reason": "No fake response configured"}}))
        return FakeReply(json.dumps({"status": "completed", "result": result}))

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


class Fakes:
    """Replaces any subset of the implement_spec Workflow's external dependencies with fakes; every other seam stays real.

    agent: the Copilot CLI, scripted. git: the Workflow's and the runtime's Git and the agent's worktree, in memory.
    github: `gh`, canned and stateful. store: the Execution ledger, in a temporary directory.
    """

    def __init__(self, seams: Collection[str], *, speed: float = 1.0, rework: bool = True) -> None:
        unknown = sorted(set(seams) - set(SEAMS))
        if unknown:
            raise ValueError(f"unknown seam {', '.join(unknown)}; choose from {', '.join(SEAMS)}")
        if "git" in seams and "agent" not in seams:
            raise ValueError("a faked git needs a faked agent: a real agent would commit where the fake git cannot see")
        self.seams = frozenset(seams)
        self._history = DryHistory()
        self.gh = DryGh()
        commit: Committer = (lambda cwd, subject: self._history.commit(subject)) if "git" in self.seams else commit_in_worktree
        self._runner = FakeCliRunner(ScriptedAgent(commit, rework), speed=speed)

    def git(self) -> tuple[GitClient, WorktreeGit] | None:
        """The in-memory Git for the Workflow and for the runtime when faked, else None."""
        return (DryGit(self._history), DryWorktreeGit(self._history)) if "git" in self.seams else None

    def github(self) -> GitHubRepo | None:
        """The canned harness repository when faked, else None."""
        return GitHubRepo(DRY_TARGET.owner, DRY_TARGET.repo, gh=self.gh) if "github" in self.seams else None

    def new_agent(self) -> AgentFactory | None:
        """Builders over the scripted agent when faked, else None; the worktree is a temporary directory only with a faked git."""
        if "agent" not in self.seams:
            return None
        worktrees = None if "git" in self.seams else GitRuntime(GitCli())
        return lambda: fake_agent(self._runner, worktrees)

    def readers(self) -> Sequence[SessionLogReader] | None:
        """The scripted agent as the session-log reader when faked, else None."""
        return (self._runner,) if "agent" in self.seams else None

    def ledger_root(self) -> Path | None:
        """A fresh temporary directory for the Execution ledger when faked, else None."""
        return Path(tempfile.mkdtemp(prefix="loop-fake-ledger-")) if "store" in self.seams else None

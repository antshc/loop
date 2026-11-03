"""In-memory Git and tracker and a scripted four-role agent, after implement_spec's dry run."""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field, replace

from ..context import AgentResult
from ..integrations import Commit
from .messages import ProposedTicket
from .tracker import PullRequest, Spec, Ticket

FIRST_TICKET = 10

_DIFF = """\
diff --git a/src/discount.py b/src/discount.py
+def discount(total): ...
diff --git a/tests/test_discount.py b/tests/test_discount.py
+def test_discount(): ...
"""


class FakeGit:
    """One commit history and set of uncommitted edits per worktree path."""

    def __init__(self) -> None:
        self._histories: dict[str, list[Commit]] = {}
        self._edits: dict[str, set[str]] = {}
        self._made = 0

    def edit(self, path: str, file: str) -> None:
        """Leaves `file` modified and uncommitted, as an agent would."""
        self._edits.setdefault(path, set()).add(file)

    def log(self, path: str) -> list[Commit]:
        """The worktree's commits after the base, oldest first."""
        return self._history(path)[1:]

    async def head(self, path: str) -> str:
        return self._history(path)[-1].sha

    async def status(self, path: str) -> str:
        return "\n".join(f" M {file}" for file in sorted(self._edits.get(path, ())))

    async def commits_since(self, path: str, sha: str) -> list[Commit]:
        history = self._history(path)
        return history[self._index(history, sha) + 1 :]

    async def branch_commits(self, path: str) -> list[Commit]:
        return self.log(path)

    async def commit(self, path: str, message: str) -> str:
        return self.commit_now(path, message)

    def commit_now(self, path: str, message: str) -> str:
        """Commits every edit of `path` and returns the new SHA."""
        self._made += 1
        sha = f"{self._made:07x}{'0' * 33}"
        self._history(path).append(Commit(sha, message.splitlines()[0]))
        self._edits.pop(path, None)
        return sha

    async def restore(self, path: str, sha: str) -> None:
        history = self._history(path)
        del history[self._index(history, sha) + 1 :]
        self._edits.pop(path, None)

    def _history(self, path: str) -> list[Commit]:
        return self._histories.setdefault(path, [Commit("0" * 40, "base")])

    @staticmethod
    def _index(history: list[Commit], sha: str) -> int:
        return next(i for i, commit in enumerate(history) if commit.sha == sha)


@dataclass
class _Issue:
    number: int
    title: str
    body: str
    state: str = "open"
    labels: list[str] = field(default_factory=list)
    blocked_by: tuple[int, ...] = ()
    comments: list[str] = field(default_factory=list)


class FakeTracker:
    """One Spec, its Tickets and the feature branch's pull request, in memory."""

    def __init__(
        self, number: int, title: str, body: str, *, diff: str = _DIFF
    ) -> None:
        self._spec = _Issue(number, title, body)
        self._tickets: list[_Issue] = []
        self._pull_requests: dict[str, PullRequest] = {}
        self._diff = diff

    def comments(self, number: int) -> list[str]:
        """The comments posted on issue `number`, oldest first."""
        return list(self._issue(number).comments)

    def labels(self, number: int) -> list[str]:
        """The labels of issue `number`."""
        return list(self._issue(number).labels)

    async def spec(self, number: int) -> Spec:
        issue = self._issue(number)
        return Spec(
            issue.number,
            issue.title,
            issue.body,
            f"https://github.com/antshc/loop/issues/{issue.number}",
            tuple(issue.labels),
            tuple(_ticket(ticket) for ticket in self._tickets),
        )

    async def ticket(self, number: int) -> Ticket:
        return _ticket(self._issue(number))

    async def create_tickets(
        self, spec: int, tickets: Sequence[ProposedTicket]
    ) -> tuple[int, ...]:
        first = FIRST_TICKET + len(self._tickets)
        for index, ticket in enumerate(tickets):
            blockers = tuple(first + blocker for blocker in ticket.blocked_by)
            self._tickets.append(
                _Issue(first + index, ticket.title, ticket.body, blocked_by=blockers)
            )
        return tuple(range(first, first + len(tickets)))

    async def comment(self, number: int, body: str) -> None:
        self._issue(number).comments.append(body)

    async def add_label(self, number: int, label: str) -> None:
        self._issue(number).labels.append(label)

    async def close(self, number: int, comment: str) -> None:
        issue = self._issue(number)
        issue.comments.append(comment)
        issue.state = "closed"

    async def open_pull_request(self, branch: str) -> PullRequest | None:
        return self._pull_requests.get(branch)

    async def publish_draft(self, branch: str, title: str) -> PullRequest:
        if branch not in self._pull_requests:
            number = 99
            url = f"https://github.com/antshc/loop/pull/{number}"
            self._pull_requests[branch] = PullRequest(number, url, branch)
        return self._pull_requests[branch]

    async def diff(self, pull_request: PullRequest) -> str:
        return self._diff

    async def mark_ready(self, pull_request: PullRequest) -> None:
        self._pull_requests[pull_request.branch] = replace(pull_request, draft=False)

    def _issue(self, number: int) -> _Issue:
        return next(
            issue for issue in (self._spec, *self._tickets) if issue.number == number
        )


def _ticket(issue: _Issue) -> Ticket:
    return Ticket(
        issue.number,
        issue.title,
        issue.body,
        issue.state,
        tuple(issue.labels),
        issue.blocked_by,
    )


_PLAN = (
    {"title": "Add discount policy", "body": "Percentage off the basket total."},
    {
        "title": "Apply discount at checkout",
        "body": "Checkout uses the policy.",
        "blocked_by": [0],
    },
)

_WORKTREE = re.compile(r"at `(?P<path>[^`]+)`")
_PREFIX = re.compile(r"subject\s+`(?P<prefix>[^`<]+)<one-line message>`")
_TICKET_JSON = re.compile(r"<ticket-json>\n(?P<json>.*?)\n</ticket-json>", re.DOTALL)
_IDENTIFIER = re.compile(r'"identifier": "(?P<identifier>[^"]+)"')
_HEAD = re.compile(r"head\s+is commit `(?P<sha>[^`]+)`")


class ScriptedFactoryAgent:
    """Answers each role by its prompt heading and records `(role, prompt)` per call.

    The planner's first reply misses `tickets` (a re-ask), Tickets in `fail_first` decline
    their first attempt (a rework), and Tickets in `decline` always decline (an escalation).
    """

    def __init__(
        self,
        git: FakeGit,
        *,
        fail_first: Collection[int] = (),
        decline: Collection[int] = (),
        findings: Sequence[dict[str, str]] = (),
        flaky_plan: bool = True,
        plan: Sequence[dict[str, object]] = _PLAN,
    ) -> None:
        self._git = git
        self._fail_first = set(fail_first)
        self._decline = set(decline)
        self._findings = list(findings)
        self._flaky_plan = flaky_plan
        self._plan = list(plan)
        self._attempts: dict[int, int] = {}
        self.calls: list[tuple[str, str]] = []

    def prompts(self, role: str) -> list[str]:
        """Every prompt the role was sent, in order."""
        return [prompt for name, prompt in self.calls if name == role]

    async def run(self, prompt: str) -> AgentResult:
        """The scripted reply of the role the prompt's heading names."""
        heading = prompt.splitlines()[0]
        role, answer = {
            "# Plan": ("planner", self._planner),
            "# Implement ticket": ("developer", self._developer),
            "# TASK": ("chore", self._chore),
            "# Review": ("reviewer", self._reviewer),
        }[heading]
        self.calls.append((role, prompt))
        return AgentResult(answer(prompt), 0)

    def _planner(self, prompt: str) -> str:
        if self._flaky_plan and len(self.prompts("planner")) == 1:
            return _answer("Here is my plan.", {"status": "completed", "result": {}})
        return _answer("Planned.", _completed({"tickets": self._plan}))

    def _developer(self, prompt: str) -> str:
        ticket = json.loads(_match(_TICKET_JSON, prompt, "json"))
        number = ticket["number"]
        attempt = self._attempts[number] = self._attempts.get(number, 0) + 1
        envelope: dict[str, object] = {
            "identifier": _match(_IDENTIFIER, prompt, "identifier")
        }
        if number in self._decline or (number in self._fail_first and attempt == 1):
            failed = {"status": "failed", "result": {"reason": "tests failed"}}
            return _answer("The checks failed.", envelope | failed)
        path = _match(_WORKTREE, prompt, "path")
        prefix = _match(_PREFIX, prompt, "prefix")
        sha = self._git.commit_now(path, f"{prefix}{ticket['title']}")
        result = {
            "commit": sha,
            "summary": ticket["title"],
            "verification": "pytest -> 12 passed",
        }
        return _answer(f"Implemented #{number}.", envelope | _completed(result))

    def _chore(self, prompt: str) -> str:
        self._git.edit(_match(_WORKTREE, prompt, "path"), "src/discount.py")
        result = {
            "commit_message": "name the discount rate",
            "summary": "Renamed `r` to `rate`; pytest -> 12 passed.",
            "out_of_scope": ["checkout rounds totals twice"],
        }
        return _answer("Refined.", _completed(result))

    def _reviewer(self, prompt: str) -> str:
        axes = [
            {"name": name, "outcome": "completed"}
            for name in ("bugs", "quality", "smells", "initiative")
        ]
        result = {
            "axes": axes,
            "findings": self._findings,
            "head": _match(_HEAD, prompt, "sha"),
        }
        return _answer("Reviewed.", _completed(result))


def _completed(result: dict[str, object]) -> dict[str, object]:
    return {"status": "completed", "result": result}


def _answer(narration: str, envelope: dict[str, object]) -> str:
    return f"{narration}\n\n```json\n{json.dumps(envelope)}\n```"


def _match(pattern: re.Pattern[str], prompt: str, group: str) -> str:
    match = pattern.search(prompt)
    if match is None:
        raise AssertionError(f"prompt has no {group}: {pattern.pattern}")
    return match[group]

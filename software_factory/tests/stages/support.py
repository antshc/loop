"""A tracker over the shared `gh` fake, a real temporary Git repository with a local bare remote and a scripted developer agent."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field, make_dataclass
from pathlib import Path
from typing import Any

from loop import AgentContext, AgentRequest, AgentResult, AgentWorkspace, RunContext, SessionName

from software_factory.events.specs import RepositoryTarget, SpecWork
from software_factory.runtime import (
    Event, Flow, Ledger, Pass, RecordingSpans, RunResult, Services, Stage, Workflow, WorkspaceOpener, run,
)
from software_factory.shared.git import GitClient
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.issues.client import Issues
from software_factory.shared.github.repo import GitHubRepo
from software_factory.shared.process import CommandRunner
from software_factory.stages import specs
from software_factory.tests.runtime.harness import GIT, Repo, command
from software_factory.tests.runtime.support import NoInput
from software_factory.tests.shared.fakes import FakeGhCli, issue

LABELS = ("spec", "repo:target:owner/repo", "repo:base:main")
TITLE = "Checkout: Add login page"
BRANCH = "add-login-page"
HITL = "hitl"
PULL_REQUEST = "https://github.com/owner/repo/pull/99"


class RecordingRunner:
    """The real process runner that remembers every command it ran."""

    def __init__(self) -> None:
        self._run = CommandRunner()
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: Sequence[str], *, cwd: Path | None = None, timeout_s: float | None = None) -> str:
        self.calls.append(tuple(args))
        return self._run(args, cwd=cwd, timeout_s=timeout_s)

    def git(self, *prefix: str) -> list[tuple[str, ...]]:
        """The recorded `git` commands whose arguments start with `prefix`."""
        return [call for call in self.calls if call[1 : 1 + len(prefix)] == prefix]


@dataclass(frozen=True)
class Probed(Event):
    """What a probe Stage produces."""


def probe(event: type[Event], seen: list[Any]) -> Stage[Any, Any, Probed]:
    """A Stage that records the latest `event` it reads, so a test can assert on what an upstream Stage produced."""
    name = re.sub(r"(?<!^)(?=[A-Z])", "-", event.__name__).lower()
    input_type = make_dataclass(f"Probe{event.__name__}", [("event", event)], frozen=True)
    return (
        Stage(f"probe-{name}", input_type, produces=Probed)
        .action(lambda i: seen.append(i.event))
        .gate(lambda *_: Pass(Probed()))
    )


class StageRig:
    """One Spec on the fake tracker, a repository with a local bare remote, a temporary ledger and recorded spans."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        labels: tuple[str, ...] = LABELS,
        tickets: list[dict] | None = None,
        blocked_by: dict[int, list[int]] | None = None,
    ) -> None:
        self.remote = tmp_path / "remote.git"
        self.remote.mkdir(parents=True)
        command(("init", "--bare", "-q", "-b", "main"), self.remote)
        self.repo = Repo(tmp_path / "checkout")
        command(("remote", "add", "origin", str(self.remote)), self.repo.path)
        command(("push", "-q", "-u", "origin", "main"), self.repo.path)
        self.gh = FakeGhCli(
            specs=[issue(1, TITLE, labels=labels)],
            tickets={1: tickets if tickets is not None else [issue(10, "Add login form")]},
            blocked_by=blocked_by,
            prs=[],
        )
        self.runner = RecordingRunner()
        self.git = GitClient(self.runner)
        self.issues = Issues(GitHubRepo("owner", "repo", gh=self.gh))
        self.github = GitHub(self.gh)
        self.ledger = Ledger(tmp_path / "ledger")
        self.spans = RecordingSpans()
        self.work = SpecWork(1, "r1")
        self.target = RepositoryTarget("owner/repo", self.repo.path, tmp_path / "worktrees")

    def route(self) -> Stage[Any, Any, Any]:
        """The routing Stage configured with this rig's repository."""
        return specs.route_spec([self.target])

    def workflow(self, *stages: Stage[Any, Any, Any]) -> Workflow[SpecWork]:
        """A Workflow of the routing Stage followed by `stages`."""
        flow = Workflow("t").context(SpecWork).stage(self.route())
        for stage in stages:
            flow = flow.stage(stage)
        return flow

    def run(self, workflow: Workflow[SpecWork], *services: object) -> RunResult:
        """Runs the Workflow for the Spec with the rig's services and any `services` given."""
        passed = Services(self.issues, self.git, self.github, GIT, *services)
        return run(workflow, self.work, services=passed, ledger=self.ledger, spans=self.spans)

    def worktree(self, script: DeveloperScript) -> WorkspaceOpener[Any]:
        """An opener of a workspace at the repository whose developer agent follows `script`."""
        workspace = AgentWorkspace(self.repo.path, lambda *_: script, RunContext(AgentContext()), None)
        return WorkspaceOpener("worktree", NoInput, lambda _: nullcontext(workspace))

    def in_worktree(
        self, script: DeveloperScript, *stages: Stage[Any, Any, Any], flow: Flow | None = None, seeds: Sequence[Event] = (),
    ) -> Workflow[SpecWork]:
        """The routing Stage, the Stages producing `seeds`, then a workspace scope of `stages` or `flow`."""
        body = flow or Flow("work")
        for stage in stages:
            body = body.stage(stage)
        return self.workflow(*(seeded(event) for event in seeds)).within(self.worktree(script), body)

    def deliver(self) -> str:
        """Checks the feature branch out with one delivered Ticket commit, pushes it and opens its draft pull request; returns the head."""
        command(("checkout", "-q", "-b", BRANCH), self.repo.path)
        self.repo.write("ticket-10-0.txt", "0\n")
        head = self.repo.commit("ccode(Checkout|10): implement 0")
        command(("push", "-q", "-u", "origin", BRANCH), self.repo.path)
        self.github.pull_requests("owner/repo").create_draft_pull_request(BRANCH, "main", TITLE, "Checkout")
        return head

    def history[T: Event](self, event: type[T]) -> tuple[T, ...]:
        """Every `event` the Spec's runs recorded, whether its Stage passed, failed or held."""
        return self.ledger.item("t", self.work.id).history(event)

    def skipped(self) -> list[str]:
        """The ids of the Stages whose condition was false, in the order they were skipped."""
        found: list[str] = []
        pending = list(self.spans.roots)
        while pending:
            span = pending.pop(0)
            found += [str(event.attributes["stage"]) for event in span.events if event.name == "factory.skipped"]
            pending += span.children
        return found

    def labels(self, number: int) -> list[str]:
        """The labels the tracker holds on an issue."""
        return [label for label in self.issues.issue(number).labels]

    def comments(self, number: int) -> list[str]:
        """The Comment bodies the tracker holds on an issue, without the hidden markers."""
        return [comment.body.split("\n\n<!--")[0] for comment in self.issues.issue(number).comments]

    def remote_head(self, branch: str) -> str:
        """The commit a branch of the bare remote points at; the remote is addressed explicitly because Git refuses implicit bare repositories."""
        return command(("--git-dir", str(self.remote), "rev-parse", branch), self.repo.path).strip()

    def branch_off_main(self, branch: str, subject: str = "work") -> str:
        """Commits on a new local branch and goes back to main; returns the branch head."""
        command(("checkout", "-q", "-b", branch), self.repo.path)
        self.repo.write(f"{branch}.txt", f"{subject}\n")
        head = self.repo.commit(subject)
        command(("checkout", "-q", "main"), self.repo.path)
        return head


@dataclass
class DeveloperScript:
    """Answers each developer run with the next action's output; an action may edit or commit in the repository."""

    repo: Repo
    actions: list[Callable[[Repo], str]]
    requests: list[AgentRequest] = field(default_factory=list)

    def run(self, request: AgentRequest) -> AgentResult:
        action = self.actions[min(len(self.requests), len(self.actions) - 1)]
        self.requests.append(request)
        return AgentResult(action(self.repo), SessionName.new(), 0)


def envelope(status: str, identifier: str, **result: str) -> str:
    """Agent output ending in the status envelope the developer role decodes."""
    return f"working...\n{json.dumps({'status': status, 'identifier': identifier, 'result': result})}\n"


def answers(status: str, **result: Any) -> Callable[[Repo], str]:
    """An agent action whose output ends in the `{status, result}` envelope of the planner, chore and reviewer roles."""
    return lambda repo: f"working...\n{json.dumps({'status': status, 'result': result})}\n"


def seeded(event: Event) -> Stage[Any, Any, Any]:
    """A Stage that produces `event` as an upstream Stage would, so a Stage under test can read it."""
    name = re.sub(r"(?<!^)(?=[A-Z])", "-", type(event).__name__).lower()
    return Stage(f"seed-{name}", NoInput, produces=type(event)).action(lambda _: event).gate(lambda *_: Pass(event))


def delivers(ticket: int, *, subject: str | None = None, report: str | None = None, commits: int = 1) -> Callable[[Repo], str]:
    """A developer action that makes `commits` commits for `ticket` and reports the last one (or `report`)."""
    def act(repo: Repo) -> str:
        head = repo.head()
        for number in range(commits):
            repo.write(f"ticket-{ticket}-{number}.txt", f"{number}\n")
            head = repo.commit(subject or f"ccode(Checkout|{ticket}): implement {number}")
        return envelope("completed", f"Checkout|{ticket}", commit=report or head, summary="Added the form.", verification="pytest passed")
    return act


def commits_nothing(ticket: int) -> Callable[[Repo], str]:
    """A developer action that edits nothing, commits nothing and reports the unchanged head."""
    return lambda repo: envelope("completed", f"Checkout|{ticket}", commit=repo.head(), summary="s", verification="v")


def declines(ticket: int, reason: str = "cannot do it") -> Callable[[Repo], str]:
    """A developer action that edits the working tree and then reports `failed`."""
    def act(repo: Repo) -> str:
        repo.write("half.txt", "half\n")
        return envelope("failed", f"Checkout|{ticket}", reason=reason)
    return act

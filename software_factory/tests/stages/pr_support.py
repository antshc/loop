"""A pull request over the shared fake host, a real repository with a local bare remote, scripted address agents and a recording check."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loop import (
    AgentBuilder, AgentContext, AgentRequest, AgentWorkspace, DockerRuntime, FakeCliRunner, FakeReply, GitCli, GitRuntime,
    RunContext,
)

from software_factory.events.feedback import FeedbackBatch, TrustPolicy
from software_factory.events.pull_requests import PullRequestPolled
from software_factory.events.specs import RepositoryTarget
from software_factory.legacy_slices.pr_babysit.watch.snapshot import babysit_snapshot
from software_factory.runtime import (
    Event, Fail, Flow, Ledger, Nothing, Pass, RecordingSpans, RunResult, Services, Stage, Workflow, WorkspaceOpener, run,
)
from software_factory.shared.agents.launcher import AgentLauncher
from software_factory.shared.git import GitClient
from software_factory.shared.github.account import GitHub
from software_factory.stages import feedback
from software_factory.tests.runtime.harness import GIT, Repo, command
from software_factory.tests.runtime.support import NoInput
from software_factory.tests.shared.fakes import FakeGhCli, FakePullRequestHost, comment_node, thread_node
from software_factory.tests.stages.support import RecordingRunner

SLUG = "owner/repo"
NUMBER = 7
BRANCH = "feature"
TRUST = TrustPolicy(
    frozenset({"OWNER", "MEMBER", "COLLABORATOR"}),
    frozenset({"copilot", "copilot-pull-request-reviewer", "chatgpt-codex-connector", "coderabbitai", "claude", "bugbot"}),
)


@dataclass(frozen=True)
class PrWork:
    """The Workflow context of one pull request."""

    number: int
    revision: str

    @property
    def id(self) -> str:
        return f"pr-{self.number}"


class Crash(Exception):
    """An agent that dies; nothing in the runtime catches it, so the run ends unfinished."""


@dataclass(frozen=True)
class PollInput:
    github: GitHub


def thread(id: str, body: str, *, line: int = 4, path: str = "cart.py") -> dict:
    """An open review thread of one reviewer comment, whose key is `thread:<id>:<id>c`."""
    return thread_node([comment_node(f"{id}c", "reviewer", body)], id=id, path=path, line=line)


def reply_text(key: str) -> str:
    """The reply the scripted agent drafts for a feedback key."""
    return f"Done: {key}"


def answer(verdict: str, *, edit: str | None = None, evidence: Sequence[str] = ("cart.py:4",), reason: str = "why") -> Callable[[Path, dict], dict]:
    """A resolve handler: optionally writes file `edit` in the worktree, then answers with `verdict` and a reply for every key."""
    def act(cwd: Path, request: dict) -> dict:
        if edit is not None:
            (cwd / edit).write_text(f"{edit}\n")
        replies = {key: reply_text(key) for key in request["issue"]["keys"]}
        return {"verdict": verdict, "evidence": list(evidence), "replies": replies, "reason": reason}
    return act


def one_issue_per_item(items: list[dict]) -> list[dict]:
    """A grouping with one issue `i1`, `i2`, ... per feedback item."""
    return [{"id": f"i{n}", "statement": f"concern {n}", "keys": [item["key"]]} for n, item in enumerate(items, 1)]


@dataclass
class AddressAgent:
    """Answers address requests: grouping through `grouping`, each issue through the handler registered for its id; remembers every request."""

    handlers: dict[str, Callable[[Path, dict], dict]]
    grouping: Callable[[list[dict]], list[dict]] = one_issue_per_item
    requests: list[dict] = field(default_factory=list)

    def __call__(self, request: AgentRequest, context: RunContext) -> FakeReply:
        payload = json.loads(request.prompt.rsplit("\n", 1)[-1])
        self.requests.append(payload)
        if payload["mode"] == "group":
            return FakeReply(json.dumps({"issues": self.grouping(payload["items"])}))
        return FakeReply(json.dumps(self.handlers[payload["issue"]["id"]](context.agent.cwd, payload)))

    def modes(self) -> list[str]:
        """The mode of each request, in order."""
        return [payload["mode"] if payload["mode"] == "group" else f"resolve:{payload['issue']['id']}" for payload in self.requests]


class Verifier:
    """A verify callable that remembers where it ran and fails for the paths it is told to."""

    def __init__(self, *, fail: Callable[[Path], bool] = lambda _: False) -> None:
        self.paths: list[Path] = []
        self._fail = fail

    def __call__(self, path: Path) -> Fail[Any] | None:
        self.paths.append(path)
        return Fail("tests failed") if self._fail(path) else None


@dataclass(frozen=True)
class Elsewhere(Event):
    """The sibling dispatch case a red baseline reroutes to."""


type Action = FeedbackBatch | Elsewhere


class PrRig:
    """A pull request on a repository with a local bare remote, a temporary ledger and recorded spans."""

    def __init__(self, tmp_path: Path, *, threads: Sequence[dict] | None = None) -> None:
        self.remote = tmp_path / "remote.git"
        self.remote.mkdir(parents=True)
        command(("init", "--bare", "-q", "-b", "main"), self.remote)
        self.repo = Repo(tmp_path / "checkout")
        command(("remote", "add", "origin", str(self.remote)), self.repo.path)
        command(("push", "-q", "-u", "origin", "main"), self.repo.path)
        command(("checkout", "-q", "-b", BRANCH), self.repo.path)
        self.repo.write("cart.py", "cart\n")
        self.head = self.repo.commit("feature work")
        command(("push", "-q", "-u", "origin", BRANCH), self.repo.path)
        command(("checkout", "-q", "main"), self.repo.path)
        self.host = FakePullRequestHost(slug=SLUG, login="me")
        self.pull = self.host.add(NUMBER, head_ref=BRANCH, head_sha=lambda: self.remote_head(BRANCH))
        self.pull.threads = list(threads if threads is not None else [thread("t1", "Handle the empty cart")])
        self.github = GitHub(FakeGhCli(pulls=self.host))
        self.runner = RecordingRunner()
        self.git = GitClient(self.runner)
        self.ledger = Ledger(tmp_path / "ledger")
        self.spans = RecordingSpans()
        self.target = RepositoryTarget(SLUG, self.repo.path, tmp_path / "worktrees")
        self.work = PrWork(NUMBER, self.head)

    def remote_head(self, branch: str) -> str:
        """The commit a branch of the bare remote points at."""
        return command(("--git-dir", str(self.remote), "rev-parse", branch), self.repo.path).strip()

    def launcher(self, agent: AddressAgent) -> AgentLauncher:
        """A launcher whose agents answer from `agent` and run real Git."""
        return AgentLauncher(
            lambda: AgentBuilder(FakeCliRunner(agent), GitRuntime(GitCli()), DockerRuntime(), AgentContext(cwd=self.repo.path)),
        )

    def poll(self) -> Stage[Any, Any, PullRequestPolled]:
        """A Stage that reads the pull request as the snapshot Stage of a definition would."""
        return (
            Stage("poll-pull-request", PollInput, produces=PullRequestPolled)
            .action(lambda i: PullRequestPolled(
                self.target, NUMBER, json.loads(json.dumps(babysit_snapshot(i.github.pull_requests(SLUG).snapshot(NUMBER)))),
            ))
            .gate(lambda _, polled, __: Pass(polled))
        )

    def workflow(self, fragment: Flow) -> Workflow[PrWork]:
        """Poll, select the trusted feedback, then the fragment."""
        return (
            Workflow("address-once").context(PrWork)
            .stage(self.poll())
            .stage(feedback.select(trust=TRUST))
            .then(fragment)
        )

    def run(self, workflow: Workflow[PrWork], agent: AddressAgent) -> RunResult:
        """Runs the Workflow for the pull request with the agent and the rig's services."""
        services = Services(self.git, self.github, GIT, self.launcher(agent))
        return run(workflow, self.work, services=services, ledger=self.ledger, spans=self.spans)

    def leave_untracked_edit(self) -> None:
        """Leaves the recovery worktree behind with an untracked file, as a run that retained its work would."""
        command(("worktree", "add", "-q", "-b", self.recovery_branch(), str(self.worktree()), self.head), self.repo.path)
        (self.worktree() / "junk.txt").write_text("unrelated\n")

    def skipped(self) -> list[str]:
        """The ids of the Stages whose condition was false, in the order they were skipped."""
        found: list[str] = []
        pending = list(self.spans.roots)
        while pending:
            span = pending.pop(0)
            found += [str(event.attributes["stage"]) for event in span.events if event.name == "factory.skipped"]
            pending += span.children
        return found

    def history[T: Event](self, event: type[T]) -> tuple[T, ...]:
        """Every `event` the pull request's runs recorded, whether its Stage passed, failed or held."""
        return self.ledger.item("address-once", self.work.id).history(event)

    def worktree(self) -> Path:
        """Where the pull request's recovery worktree lives."""
        return self.target.worktrees / "babysit" / SLUG / str(NUMBER)

    def recovery_branch(self) -> str:
        """The branch the recovery worktree is on, which keeps its commits."""
        return f"babysit/{SLUG}/{NUMBER}"

    def subjects(self) -> list[str]:
        """The commit subjects the recovery branch adds to the pull request head, oldest first."""
        out = command(("log", "--reverse", "--format=%s", f"{self.head}..{self.recovery_branch()}"), self.repo.path)
        return out.splitlines()

    def replies(self) -> list[str]:
        """Every reply and Comment the host recorded."""
        return list(self.host.replies)


def workspace_at(path: Path) -> AgentWorkspace:
    """A workspace over `path` whose agent is never used."""
    return AgentWorkspace(path, lambda *_: None, RunContext(AgentContext()), None)


def in_workspace(path: Path) -> WorkspaceOpener[Any]:
    """An opener of the workspace over `path`, for Stages that need a worktree but no agent."""
    return WorkspaceOpener("workspace", NoInput, lambda _: nullcontext(workspace_at(path)))


def blocking(blocked: list[str]) -> Stage[Any, Any, Nothing]:
    """A hand-off Stage that records it ran."""
    return Stage("block-pr", NoInput, produces=Nothing).action(lambda _: blocked.append("blocked")).gate(lambda *_: Pass(Nothing()))


def selected_batch(rig: PrRig) -> FeedbackBatch:
    """The batch the select Stage makes of the rig's pull request."""
    polled = PullRequestPolled(rig.target, NUMBER, json.loads(json.dumps(babysit_snapshot(rig.github.pull_requests(SLUG).snapshot(NUMBER)))))
    return feedback.select(trust=TRUST).executor.run(feedback.SelectInput(polled, (), rig.github))

"""A Spec on the fake tracker, a real temporary repository, a scripted agent and a temporary ledger, driven through the implement_spec entry point."""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from loop import AgentRequest, FakeCliRunner, FakeReply, GitCli, GitRuntime, RunContext, fake_agent

from loop import RecordingSpans, Services
from workflows.software_factory.infrastructure.agents.launcher import AgentLauncher
from tests.runtime.harness import GIT, command
from tests.shared.fakes import issue
from workflows.tests.software_factory.stages.support import BRANCH, LABELS, StageRig, envelope
from workflows.software_factory.workflows.implement_spec.__main__ import Runtime, main
from workflows.software_factory.workflows.implement_spec.dry import commit_in_worktree
from workflows.software_factory.workflows.implement_spec.settings import Targets

TICKET_PROMPT = re.compile(r"subject `ccode\((?P<identifier>[^)|]+\|(?P<number>\d+))\): ")

REVIEW_AXES = ("bugs", "quality", "smells", "initiative")

TWO_TICKETS = [issue(10, "Add login form"), issue(11, "Add login tests")]


class Died(BaseException):
    """Stands for a process that was killed while the agent was working."""


class StopAfter:
    """The interrupt signal: it fires once the agent has been asked to implement the Ticket it waits for."""

    def __init__(self) -> None:
        self.after: int | None = None
        self.asked: set[int] = set()

    def requested(self) -> bool:
        """Whether the awaited Ticket has been asked for."""
        return self.after in self.asked


class Agents:
    """Answers every role's prompt; a Ticket's attempts follow its actions (`commit`, `fail`, `nothing`, `die`), the last one repeating; the chore writes `chore_edit` when set."""

    def __init__(self, stop: StopAfter) -> None:
        self.stop = stop
        self.actions: dict[int, list[str]] = {}
        self.planned: list[dict[str, Any]] | None = None
        self.prompts: dict[str, list[str]] = {"developer": [], "planner": [], "chore": [], "reviewer": []}
        self.attempts: dict[int, int] = {}
        self.chore_edit: str | None = None

    def developer_runs(self, ticket: int | None = None) -> int:
        """How many developer runs there were, in total or for one Ticket."""
        return len(self.prompts["developer"]) if ticket is None else self.attempts.get(ticket, 0)

    def __call__(self, request: AgentRequest, context: RunContext) -> FakeReply:
        match = TICKET_PROMPT.search(request.prompt)
        if match is not None:
            self.prompts["developer"].append(request.prompt)
            return self._developer(match["identifier"], int(match["number"]), context.agent.cwd)
        if request.prompt.startswith("# Plan"):
            self.prompts["planner"].append(request.prompt)
            return _reply("failed", reason="needs a product decision") if self.planned is None else _reply("completed", tickets=self.planned)
        if request.prompt.startswith("# TASK"):
            self.prompts["chore"].append(request.prompt)
            if self.chore_edit is None:
                return _reply("completed", summary="No cleanup needed", out_of_scope=[])
            (context.agent.cwd / "chore.txt").write_text(self.chore_edit)
            return _reply("completed", summary="Tidied", out_of_scope=[], commit_message="Tidy the change set")
        self.prompts["reviewer"].append(request.prompt)
        head = re.search(r"head is commit\s+`([^`]+)`", request.prompt)
        axes = [{"name": name, "outcome": "completed"} for name in REVIEW_AXES]
        return _reply("completed", axes=axes, head=head[1] if head else "", findings=[])

    def _developer(self, identifier: str, ticket: int, cwd: Path) -> FakeReply:
        attempt = self.attempts[ticket] = self.attempts.get(ticket, 0) + 1
        actions = self.actions.get(ticket, ["commit"])
        action = actions[min(attempt, len(actions)) - 1]
        self.stop.asked.add(ticket)
        if action == "die":
            raise Died(f"died while implementing #{ticket}")
        if action == "fail":
            return FakeReply(envelope("failed", identifier, reason=f"tests for #{ticket} failed"))
        if action == "nothing":
            sha = command(("rev-parse", "HEAD"), cwd).strip()
        else:
            sha = commit_in_worktree(cwd, f"ccode({identifier}): Ticket {ticket}")
        return FakeReply(envelope("completed", identifier, commit=sha, summary=f"Did #{ticket}.", verification="pytest passed"))


def _reply(status: str, **result: Any) -> FakeReply:
    return FakeReply(f"working...\n{json.dumps({'status': status, 'result': result})}\n")


class ImplementSpecHarness:
    """One Spec of the rig's tracker, delivered by `main` through a scripted agent over the rig's real repository."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        labels: tuple[str, ...] = LABELS,
        tickets: list[dict] | None = None,
        blocked_by: dict[int, list[int]] | None = None,
    ) -> None:
        self.rig = StageRig(tmp_path, labels=labels, tickets=tickets, blocked_by=blocked_by)
        self.log_dir = tmp_path / "logs"
        self.agents = Agents(StopAfter())
        runner = FakeCliRunner(self.agents, speed=0.0)
        launcher = AgentLauncher(lambda: fake_agent(runner, GitRuntime(GitCli())), None, (runner,))
        self.spans = RecordingSpans()
        self.services = Services(
            self.rig.issues, self.rig.git, self.rig.github, GIT, launcher, Targets((self.rig.target,)),
            Runtime(self.rig.ledger, self.spans, self.agents.stop),
        )

    def run(self, *argv: str) -> int:
        """Runs the entry point over the rig, as a person would, and returns its exit code; the process runs in the rig's checkout, never in this repository's."""
        with contextlib.chdir(self.rig.repo.path):
            return main(["--log-dir", str(self.log_dir), "--no-otel", *argv], services=self.services)

    def restart(self) -> None:
        """Starts the next process: a fresh interrupt signal and fresh spans over the same ledger and tracker."""
        self.agents.stop = StopAfter()
        self.spans = RecordingSpans()
        self.services = self.services.replace(Runtime(self.rig.ledger, self.spans, self.agents.stop))

    def labels(self, number: int) -> set[str]:
        """The labels the tracker holds on an issue."""
        return set(self.rig.labels(number))

    def closed(self) -> list[int]:
        """The Tickets closed, in order."""
        return [int(call[2]) for call in self.rig.gh.calls_matching("issue", "close")]

    def pull_request_creations(self) -> int:
        """How many draft pull requests were created."""
        return len(self.rig.gh.calls_matching("pr", "create"))

    def outcomes(self, stage_id: str) -> list[str]:
        """The pass, fail or hold of every attempt of a Stage in the last run, in order."""
        return [str(span.attributes["factory.stage.outcome"]) for span in self.spans.attempts(stage_id)]

    def opened_worktree(self) -> bool:
        """Whether a worktree was ever created under the target's worktrees folder."""
        return self.rig.target.worktrees.exists()

    def branch_head(self) -> str:
        """The feature branch's head in the checkout's repository."""
        return command(("rev-parse", BRANCH), self.rig.repo.path).strip()

    def branch_subjects(self) -> list[str]:
        """The subjects of the feature branch's commits since main, oldest first."""
        return command(("log", "--reverse", "--format=%s", f"main..{BRANCH}"), self.rig.repo.path).splitlines()

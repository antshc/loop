"""A pull request under watch: the shared pull request rig with the pull request's own work item, a pacer that never sleeps and a scripted maintainer."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loop import AgentRequest, FakeReply, RunContext

from software_factory.events.pull_requests import PullRequestPolled, PullRequestWork
from software_factory.runtime import Event, Fail, Nothing, Pass, RunResult, Services, Stage, StopSignal, Workflow, run
from software_factory.shared.pacing import Pacer
from software_factory.stages import pull_requests
from tests.runtime.harness import GIT
from tests.runtime.support import NoInput
from tests.shared.fakes import check_run, workflow_run
from tests.stages.pr_support import BRANCH, NUMBER, SLUG, PrRig

WORKFLOW = "watch"


def watch(*nodes: Stage[Any, Any, Any]) -> Workflow[PullRequestWork]:
    """A Workflow of the Stages in order over the pull request work item."""
    workflow = Workflow(WORKFLOW).context(PullRequestWork)
    for node in nodes:
        workflow = workflow.stage(node)
    return workflow


def failing(reason: str) -> Stage[Any, Any, Nothing]:
    """A check Stage that fails with `reason`."""
    return Stage("fails", NoInput, produces=Nothing).gate(lambda *_: Fail(reason))


def produces(event: Event) -> Stage[Any, Any, Any]:
    """A Stage that produces `event` but is always skipped, so a History of it has a producer and nothing is recorded."""
    name = re.sub(r"(?<!^)(?=[A-Z])", "-", type(event).__name__).lower()
    return Stage(f"produce-{name}", NoInput, produces=type(event)).gate(lambda *_: Pass(event)).when(lambda _: False)


def fingerprint_of(polled: PullRequestPolled) -> str:
    """A fingerprint that shows how many conversation items the read saw."""
    return f"{polled.head}:{len(polled.snapshot['other'])}"


def fix_file(cwd: Path) -> dict:
    """A fix handler that edits `fix.txt` and says it fixed CI."""
    (cwd / "fix.txt").write_text("fixed\n")
    return {"verdict": "fixed", "reason": "repaired"}


@dataclass
class MaintainerAgent:
    """Answers maintainer requests: diagnose with `classification`, fix through `fix`; remembers every request."""

    classification: str = "branch"
    reason: str = "CI evidence"
    fix: Callable[[Path], dict] = fix_file
    requests: list[dict] = field(default_factory=list)

    def __call__(self, request: AgentRequest, context: RunContext) -> FakeReply:
        payload = json.loads(request.prompt.rsplit("\n", 1)[-1])
        self.requests.append(payload)
        if payload["mode"] == "diagnose":
            return FakeReply(json.dumps({"classification": self.classification, "reason": self.reason}))
        return FakeReply(json.dumps(self.fix(context.agent.cwd)))

    def modes(self) -> list[str]:
        """The mode of each request, in order."""
        return [payload["mode"] for payload in self.requests]


class WatchRig(PrRig):
    """The pull request of the shared rig without feedback, owned by the repository it is watched in, with the pull request as the work item."""

    def __init__(self, tmp_path: Path, *, threads: list[dict] | None = None) -> None:
        super().__init__(tmp_path, threads=[] if threads is None else threads)
        self.pull.head_repo = SLUG
        self.waits: list[float] = []
        self.pacer = Pacer(self.waits.append)
        self.watched = PullRequestWork(self.target, NUMBER, self.head)

    def fail_ci(self, *, runs: tuple[int, ...] = (10,), attempt: int = 1) -> None:
        """Makes the head's CI fail with the given workflow runs."""
        self.pull.workflow_runs = lambda: [workflow_run(run, self.head_now(), conclusion="failure", workflow_id=run, attempt=attempt) for run in runs]

    def pend_ci(self) -> None:
        """Makes the head's CI still running."""
        self.pull.check_runs = lambda: [check_run(1, "test", status="in_progress", conclusion=None, commit=self.head_now())]

    def head_now(self) -> str:
        """The pull request's head commit on the remote."""
        return self.remote_head(BRANCH)

    def run_watch(
        self, workflow: Workflow[PullRequestWork], agent: MaintainerAgent | None = None, *, stop: StopSignal | None = None,
    ) -> RunResult:
        """Runs the Workflow for the watched pull request with the maintainer, the pacer and the rig's services."""
        services = Services(self.git, self.github, GIT, self.launcher(agent or MaintainerAgent()), self.pacer)
        return run(workflow, self.watched, services=services, ledger=self.ledger, spans=self.spans, stop=stop)

    def recorded[T: Event](self, event: type[T]) -> tuple[T, ...]:
        """Every `event` the watched pull request's runs recorded."""
        return self.ledger.item(WORKFLOW, self.watched.id).history(event)

    def closed_run_attributes(self) -> dict[str, Any]:
        """The attributes of the last closed run of the watched pull request."""
        records = self.ledger.item(WORKFLOW, self.watched.id).records()
        return next(record["attributes"] for record in reversed(records) if record["kind"] == "run-closed")

    def polled(self) -> PullRequestPolled:
        """The pull request read the way the snapshot Stage reads it."""
        return pull_requests.snapshot().executor.run(pull_requests.SnapshotInput(self.watched, self.github))

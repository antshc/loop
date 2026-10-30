"""Self-contained Workflow: plan, implement, and report commits on one worktree without pushing.

The steps are a declarative definition on Loop's runtime: plan, require a plan, record HEAD, implement and report
the commits, all in one worktree scope. Every Stage owns no repository contract, so committing stays permissive.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from loop import (
    Agent,
    AgentBuilder,
    AgentProfile,
    AgentRunError,
    AgentWorkspace,
    BranchStrategy,
    Event,
    Fail,
    Flow,
    GitOptions,
    Ledger,
    Message,
    Nothing,
    Pass,
    Role,
    Services,
    Stage,
    TypedAgent,
    Verdict,
    Workflow,
    WorkspaceOpener,
    copilot,
    next_,
    run,
)

logger = logging.getLogger("workflow.plan_implement")

PLAN_MODEL = "claude-opus-5.5"
PLAN_EFFORT = "max"
IMPLEMENT_MODEL = "claude-sonnet-5.5"
IMPLEMENT_EFFORT = "high"

PLAN_INSTRUCTIONS = """You are planning a coding task on this repository. Do not write or change any code.

Write a concise, actionable implementation plan for another agent to follow.

End your final message with exactly one of these tags, with nothing after it:
<completed>Success</completed>
<completed>Failure: <why></completed>
"""

IMPLEMENT_INSTRUCTIONS = """You are implementing a coding task on this repository, following the plan above.

Implement the plan. Commit your work with git. Do not push.

End your final message with exactly one of these tags, with nothing after it:
<completed>Success</completed>
<completed>Failure: <why></completed>
"""

type GitReader = Callable[[Path, list[str]], str]


@dataclass(frozen=True)
class Task:
    """The work item: the free-text task of one run."""

    text: str
    id: str
    revision: str


@dataclass(frozen=True)
class Prompt(Message):
    """What one agent run is asked."""

    text: str


@dataclass(frozen=True)
class Response:
    """An agent's response body, or why it is unusable."""

    body: str | None
    error: str | None = None


@dataclass(frozen=True)
class Planned(Event):
    plan: str


@dataclass(frozen=True)
class HeadRecorded(Event):
    sha: str


@dataclass(frozen=True)
class Implemented(Event):
    """The implementer reported success."""


@dataclass(frozen=True)
class Worktrees:
    """Opens the run's single worktree."""

    new_agent: Callable[[], AgentBuilder]
    options: GitOptions

    @contextmanager
    def open(self) -> Iterator[AgentWorkspace]:
        with self.new_agent().with_git(self.options).open() as worktree:
            yield worktree


@dataclass(frozen=True)
class Git:
    """Reads Git facts of the worktree."""

    read: GitReader


@dataclass(frozen=True)
class OpenInput:
    worktrees: Worktrees


@dataclass(frozen=True)
class PlanInput:
    task: Task


@dataclass(frozen=True)
class RequirePlanInput:
    planned: Planned | None


@dataclass(frozen=True)
class HeadInput:
    workspace: AgentWorkspace
    git: Git


@dataclass(frozen=True)
class ImplementInput:
    # Optional only for the definition check: the require-plan Stage stops the run before this without a plan.
    planned: Planned | None

    def prompt(self) -> Prompt:
        assert self.planned is not None
        return Prompt(f"{self.planned.plan}\n\n{IMPLEMENT_INSTRUCTIONS}")


@dataclass(frozen=True)
class ReportInput:
    head: HeadRecorded
    implemented: Implemented | None
    workspace: AgentWorkspace
    git: Git


def _response_body(text: str) -> str:
    """Returns the text before a success tag; raises ValueError for missing or unsuccessful tags."""
    match = re.search(r"<completed>(.*?)</completed>", text, re.DOTALL)
    if match is None:
        raise ValueError("no <completed> tag in agent output")
    status = match.group(1).strip()
    if status.lower() != "success":
        raise ValueError(f"agent reported: {status}")
    return text[: match.start()].strip()


def _decode(_: Prompt, output: str) -> Response:
    try:
        return Response(_response_body(output))
    except ValueError as exception:
        return Response(None, str(exception))


@dataclass(frozen=True)
class LoggingAgent[Q: Message, R](TypedAgent[Q, R]):
    """A typed agent that logs a failed agent command with its stderr before reporting the failure."""

    def run(self, workspace: AgentWorkspace, request: Q, *, session: str | None = None) -> R:
        try:
            return super().run(workspace, request, session=session)
        except AgentRunError as exception:
            cause = exception.__cause__
            if isinstance(cause, subprocess.CalledProcessError):
                logger.error("agent command failed: %s; stderr: %s", cause, cause.stderr)
            raise


def _agent(role: str, profile: AgentProfile) -> TypedAgent[Prompt, Response]:
    return LoggingAgent(role, profile, lambda prompt: prompt.text, _decode)


def _usable(response: Response) -> Verdict[Planned]:
    if response.error is not None:
        logger.error("%s", response.error)
        return Fail(response.error)
    if not response.body:
        return Fail("empty plan")
    return Pass(Planned(response.body))


def _require_plan(planned: RequirePlanInput, _: Nothing, __: object) -> Verdict[Nothing]:
    if planned.planned is None:
        logger.error("planning run failed: no usable plan")
        return Fail("no usable plan")
    return Pass(Nothing())


def _implemented(_: ImplementInput, response: Response, __: object) -> Verdict[Implemented]:
    if response.error is not None:
        logger.error("%s", response.error)
        return Fail(response.error)
    return Pass(Implemented())


def _report(report: ReportInput) -> str:
    commits = report.git.read(report.workspace.path, ["log", "--reverse", "--format=%H", f"{report.head.sha}..HEAD"])
    logger.info("commits: %s", ", ".join(commits.splitlines()) or "none")
    return commits


def _reported(report: ReportInput, _: str, __: object) -> Verdict[Nothing]:
    if report.implemented is None:
        logger.error("implementing run failed")
        return Fail("implementing run failed")
    return Pass(Nothing())


def plan_implement(planner: AgentProfile, implementer: AgentProfile) -> Workflow[Task]:
    """The definition: one worktree scope that plans, then implements, then reports the commits."""
    return (
        Workflow("plan_implement").context(Task)
        .within(
            WorkspaceOpener("worktree", OpenInput, lambda opened: opened.worktrees.open()),
            Flow("plan-and-implement")
            .stage(
                Stage("plan", PlanInput, Planned)
                .agent(_agent(Role.PLANNER, planner), lambda i, _: Prompt(f"{i.task.text}\n\n{PLAN_INSTRUCTIONS}"))
                .gate(lambda _, response, __: _usable(response))
                .on_fail(next_())
            )
            .stage(Stage("require-plan", RequirePlanInput, Nothing).gate(_require_plan))
            .stage(
                Stage("record-head", HeadInput, HeadRecorded)
                .action(lambda i: i.git.read(i.workspace.path, ["rev-parse", "HEAD"]))
                .gate(lambda _, sha, __: Pass(HeadRecorded(sha)))
            )
            .stage(
                Stage("implement", ImplementInput, Implemented)
                .agent(_agent(Role.DEVELOPER, implementer), lambda i, _: i.prompt())
                .gate(_implemented)
                .on_fail(next_())
            )
            .stage(Stage("report-commits", ReportInput, Nothing).action(_report).gate(_reported)),
        )
    )


def _git_output(path: Path, args: list[str]) -> str:
    return subprocess.run(
        ["git", "--no-pager", *args], cwd=path, check=True, capture_output=True, text=True,
    ).stdout.strip()


def main(
    argv: list[str] | None = None,
    *,
    plan_model: str = PLAN_MODEL,
    plan_effort: str = PLAN_EFFORT,
    implement_model: str = IMPLEMENT_MODEL,
    implement_effort: str = IMPLEMENT_EFFORT,
    read_git: GitReader = _git_output,
    new_agent: Callable[[], AgentBuilder] = Agent,
) -> int:
    """Plans and implements a task in one worktree; returns 0 on success, 1 on step failure, or 130 on interrupt."""
    parser = argparse.ArgumentParser(prog="plan_implement")
    parser.add_argument("task")
    args = parser.parse_args(argv)
    planner = AgentProfile(copilot, plan_model, plan_effort, name=Role.PLANNER)
    implementer = AgentProfile(copilot, implement_model, implement_effort, name=Role.DEVELOPER)
    branch = f"loop/run-{uuid.uuid4().hex[:8]}"
    logger.info("branch %s", branch)
    options = GitOptions(
        root_path=Path("workspace") / f"{Path.cwd().name}.worktrees",
        strategy=BranchStrategy(branch, "origin/main"),
    )
    services = Services(Worktrees(new_agent, options), Git(read_git))

    # The run needs no resume, so its Execution ledger is thrown away with the run.
    with tempfile.TemporaryDirectory(prefix="plan_implement-") as ledger_root:
        try:
            result = run(
                plan_implement(planner, implementer), Task(args.task, branch, branch),
                services=services, ledger=Ledger(Path(ledger_root)),
            )
        except KeyboardInterrupt:
            logger.warning("interrupted")
            return 130
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

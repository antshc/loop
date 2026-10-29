"""Work items, repository identities, and outcomes used by implement_spec."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loop import AgentWorkspace

from software_factory.shared.git import Commit  # noqa: F401  (re-exported for the slices)
from software_factory.shared.github.identity import RepositoryId
from software_factory.shared.github.issues.graph import SpecGraphSnapshot
from software_factory.shared.github.issues.spec import HITL_LABEL, Ticket  # noqa: F401  (HITL_LABEL is re-exported for the slices)


def loop_session_name(number: int, title: str) -> str:
    """Names an agent session for its source Spec."""
    return f"loop({number}) {' '.join(title.split())}"


@dataclass(frozen=True)
class Repository:
    """One repository's checkout, worktree directory, and GitHub identity."""

    checkout: Path
    worktrees: Path
    owner_name: RepositoryId


@dataclass(frozen=True)
class AgentCommitSubject:
    """The response identifier and required commit-subject prefix for one Ticket."""

    initiative: str
    ticket_number: int

    def __str__(self) -> str:
        return f"{self.initiative}|{self.ticket_number}"

    @property
    def initiative_commit_prefix(self) -> str:
        """The prefix shared by all delivering commits for this Initiative."""
        return f"ccode({self.initiative}|"

    def to_subject(self) -> str:
        """The required prefix of a Ticket's delivering commit subject."""
        return f"ccode({self}): "


class TicketWorkflowError(Exception):
    """An expected failure in ticket delivery or its agent delegate."""


@dataclass(frozen=True)
class TicketContext:
    """The selected Ticket and branch history supplied to one agent-workflow attempt."""

    ticket: Ticket
    identifier: AgentCommitSubject
    initiative_commits: tuple[str, ...]
    base_branch: str
    feature_branch: str


@dataclass(frozen=True)
class TicketResult:
    """A delegate's completed commit, summary and verification, or its failure reason."""

    identifier: str
    status: str
    commit: str | None = None
    summary: str | None = None
    verification: str | None = None
    reason: str | None = None


AgentWorkflow = Callable[[TicketContext, AgentWorkspace], TicketResult]


@dataclass(frozen=True)
class PlannedTicket:
    """One Ticket the planner proposes; `blocked_by` holds zero-based indexes of earlier planned Tickets."""

    title: str
    body: str
    blocked_by: tuple[int, ...] = ()


@dataclass(frozen=True)
class PlanContext:
    spec: SpecGraphSnapshot
    base_branch: str
    session_name: str | None = None
    source_root: Path | None = None


@dataclass(frozen=True)
class PlanResult:
    """The planned Tickets in dependency order, or why the Spec cannot be planned."""

    status: str
    tickets: tuple[PlannedTicket, ...] = ()
    reason: str | None = None


PlanWorkflow = Callable[[PlanContext, AgentWorkspace], PlanResult]


@dataclass(frozen=True)
class ChoreContext:
    """The published pull request and its captured per-file diff, supplied to one chore agent run."""

    pull_request_url: str
    base_branch: str
    feature_branch: str
    diff_dir: Path
    branch_commits: tuple[str, ...] = ()
    session_name: str | None = None


@dataclass(frozen=True)
class ChoreResult:
    """The chore agent's proposed commit message and the findings it left alone; both may be empty."""

    commit_message: str = ""
    summary: str = ""
    out_of_scope: tuple[str, ...] = ()


ChoreWorkflow = Callable[[ChoreContext, AgentWorkspace], ChoreResult]


@dataclass(frozen=True)
class ReviewContext:
    pull_request_url: str
    source_root: Path
    initiative: str
    head_sha: str
    session_name: str | None = None


@dataclass(frozen=True)
class ReviewAxis:
    name: str
    outcome: str
    note: str = ""


# Findings of these kinds keep the pull request a draft; quality and smell findings are only reported.
BLOCKING_FINDING_KINDS = frozenset({"bug", "initiative"})


@dataclass(frozen=True)
class ReviewFinding:
    kind: str
    summary: str
    axis: str = ""


@dataclass(frozen=True)
class ReviewResult:
    axes: tuple[ReviewAxis, ...] = ()
    findings: tuple[ReviewFinding, ...] = ()
    posted: tuple[str, ...] = ()
    head_sha: str = ""

    @property
    def completed(self) -> bool:
        return {axis.name for axis in self.axes} == {"bugs", "quality", "smells", "initiative"} and all(axis.outcome == "completed" for axis in self.axes)

    @property
    def blocking_findings(self) -> tuple[ReviewFinding, ...]:
        return tuple(finding for finding in self.findings if finding.kind in BLOCKING_FINDING_KINDS)


ReviewWorkflow = Callable[[ReviewContext, AgentWorkspace], ReviewResult]

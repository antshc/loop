"""Work items, repository identities, and outcomes used by dev."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from loop import AgentWorkspace

from workflows.shared.git import Commit  # noqa: F401  (re-exported for the slices)
from workflows.shared.github.pull_requests import PullRequest  # noqa: F401


def loop_session_name(number: int, title: str) -> str:
    """Names an agent session for its source Spec."""
    return f"loop({number}) {' '.join(title.split())}"


class RepositoryId(NamedTuple):
    """GitHub owner and repository name, represented as an owner/repo slug."""

    owner: str
    repo: str

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}"


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


HITL_LABEL = "hitl"


_BLOCKING_LABELS = frozenset({HITL_LABEL, "spec"})


_INITIATIVE = re.compile(r"^(?P<initiative>[^:]+):\s*(?P<title>.+)$")


_TARGET_PREFIX = "repo:target:"


_BASE_PREFIX = "repo:base:"


_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


_VERSION = re.compile(r"(\d+(?:\.\d+)+)")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "spec"


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    created_at: str


@dataclass(frozen=True)
class _CommentOn:
    number: int
    body: str


@dataclass(frozen=True)
class _AddLabel:
    number: int
    label: str


@dataclass(frozen=True)
class _Close:
    number: int
    comment: str


_Change = _CommentOn | _AddLabel | _Close


@dataclass
class Spec:
    """Aggregate root: its Tickets change only through it, and `TicketsTracker.update_spec` persists the changes."""

    number: int
    title: str
    url: str
    labels: tuple[str, ...]
    body: str = ""
    comments: tuple[Comment, ...] = ()
    # Open Tickets ready for delivery, in order; they live on the harness tracker even when the Spec targets another repo.
    tickets: tuple[Ticket, ...] = ()
    sub_issue_total: int = 0
    held_tickets: tuple[int, ...] = ()
    # Changes made since the last update_spec, in the order GitHub must see them.
    _changes: list[_Change] = field(default_factory=list, init=False, repr=False, compare=False)

    @property
    def awaiting_human(self) -> bool:
        """Carries the `hitl` label."""
        return HITL_LABEL in self.labels

    @property
    def has_work(self) -> bool:
        """Has at least one Ticket ready for delivery."""
        return any(ticket.actionable for ticket in self.tickets)

    @property
    def needs_plan(self) -> bool:
        """Has no sub-issues or locally supplied Tickets."""
        return self.sub_issue_total == 0 and not self.tickets

    @property
    def pending_changes(self) -> tuple[_Change, ...]:
        """Changes not yet persisted, oldest first."""
        return tuple(self._changes)

    def acknowledge(self, change: _Change) -> None:
        """Marks the oldest occurrence of `change` as persisted."""
        self._changes.remove(change)

    def comment(self, message: str) -> None:
        """Comments `message` on the Spec."""
        self._changes.append(_CommentOn(self.number, message))

    def hand_to_human(self, message: str) -> None:
        """Labels the Spec `hitl` and comments `message` on it."""
        self.labels = _with_label(self.labels, HITL_LABEL)
        self._changes += [_AddLabel(self.number, HITL_LABEL), _CommentOn(self.number, message)]

    def block(self, message: str) -> None:
        """Hands the Spec to a human, but only when there is work it is blocking."""
        if self.has_work:
            self.hand_to_human(message)

    def escalate(self, ticket_number: int, reason: str) -> None:
        """Hands both the Ticket and the Spec to a human."""
        message = f"dev: {reason}"
        ticket = self._ticket(ticket_number)
        ticket.labels = _with_label(ticket.labels, HITL_LABEL)
        self._changes += [_AddLabel(ticket_number, HITL_LABEL), _CommentOn(ticket_number, message)]
        self.hand_to_human(message)

    def close_ticket(self, ticket_number: int, comment: str) -> None:
        """Closes a delivered Ticket with `comment`."""
        self._ticket(ticket_number).state = "closed"
        self._changes.append(_Close(ticket_number, comment))

    def announce_delivered(self, pull_request_url: str) -> None:
        """Tells the Spec that all its Tickets are delivered and where the draft PR is."""
        self.comment(f"dev: all Tickets delivered; draft pull request: {pull_request_url}")

    def _ticket(self, number: int) -> Ticket:
        for ticket in self.tickets:
            if ticket.number == number:
                return ticket
        raise ValueError(f"ticket #{number} does not belong to spec #{self.number}")

    @property
    def initiative(self) -> str:
        """The `<initiative>: <title>` title prefix, or the Spec number when absent."""
        match = _INITIATIVE.match(self.title)
        return str(self.number) if match is None else match["initiative"].strip()

    @property
    def bare_title(self) -> str:
        """The title without its Initiative prefix."""
        match = _INITIATIVE.match(self.title)
        return self.title if match is None else match["title"].strip()

    @property
    def target(self) -> str | None:
        """The single `repo:target:<owner/name>` label's value, or None when missing/malformed/duplicated."""
        values = [label[len(_TARGET_PREFIX) :] for label in self.labels if label.startswith(_TARGET_PREFIX)]
        if len(values) != 1 or not _SLUG.match(values[0]):
            return None
        return values[0]

    @property
    def base_branch(self) -> str | None:
        """The single `repo:base:<branch>` label's value, or None when missing/empty/duplicated."""
        values = [label[len(_BASE_PREFIX) :] for label in self.labels if label.startswith(_BASE_PREFIX)]
        return values[0] if len(values) == 1 and values[0] else None

    @property
    def feature_branch(self) -> str | None:
        """`<version_with_underscores>_<title slug>` when base_branch carries a version, else `<title slug>`; None without a base_branch."""
        base_branch = self.base_branch
        if base_branch is None:
            return None
        slug = slugify(self.bare_title)
        match = _VERSION.search(base_branch)
        return slug if match is None else f"{match[1].replace('.', '_')}_{slug}"


@dataclass
class Ticket:
    number: int
    title: str
    state: str
    labels: tuple[str, ...]
    url: str
    body: str = ""
    comments: tuple[Comment, ...] = ()

    @property
    def actionable(self) -> bool:
        """Open and carrying neither the `hitl` nor the `spec` label."""
        return self.state == "open" and not ({label.casefold() for label in self.labels} & _BLOCKING_LABELS)


def _with_label(labels: tuple[str, ...], label: str) -> tuple[str, ...]:
    return labels if label in labels else (*labels, label)


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
    spec: Spec
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


VERDICTS = ("pass", "partial", "fail", "inconclusive")


@dataclass(frozen=True)
class VerifyFinding:
    summary: str
    status: str
    reason: str = ""


@dataclass(frozen=True)
class VerifyContext:
    """What the verifier needs to drive the app on the integrated branch."""

    pull_request_url: str
    base_branch: str
    feature_branch: str
    evidence_dir: Path
    skills: tuple[Path, ...]
    spec_json: str
    tickets_json: str
    branch_commits: tuple[str, ...] = ()
    session_name: str | None = None


@dataclass(frozen=True)
class VerifyResult:
    """The verifier's verdict, findings and proposed message for the single fix commit the harness may make."""

    verdict: str
    summary: str = ""
    verification: str = ""
    findings: tuple[VerifyFinding, ...] = ()
    commit_message: str = ""


VerifyWorkflow = Callable[[VerifyContext, AgentWorkspace], VerifyResult]


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

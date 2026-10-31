"""The Spec work item and the Events of routing, preparing, announcing and handing off a Spec."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loop import Event


@dataclass(frozen=True)
class SpecWork:
    """The Workflow context of one Spec: its tracker number and the revision of what it is about."""

    number: int
    revision: str

    @property
    def id(self) -> str:
        """The work-item identity the ledger and spans use."""
        return f"spec-{self.number}"


@dataclass(frozen=True)
class RepositoryTarget:
    """One configured repository a Spec can be delivered in, and which optional activities run there."""

    slug: str
    checkout: Path
    worktrees: Path
    plan: bool = True
    chore: bool = True
    review: bool = True


@dataclass(frozen=True)
class SpecRouted(Event):
    """The Spec's configured target with its Initiative and branches."""

    target: RepositoryTarget
    initiative: str
    base_branch: str
    feature_branch: str

    def subject_prefix(self, ticket: int) -> str:
        """The prefix of the one commit that delivers Ticket `ticket`."""
        return f"ccode({self.initiative}|{ticket}): "


@dataclass(frozen=True)
class SpecHeld(Event):
    """Why a Stage held the Spec, kept as evidence for the hand-off that follows."""

    reason: str


@dataclass(frozen=True)
class CheckoutPrepared(Event):
    """The checkout was fetched and the Spec's base branch exists on origin."""

    base_branch: str


@dataclass(frozen=True)
class WorkFound(Event):
    """The Spec has actionable, READY Tickets."""

    tickets: tuple[int, ...]


@dataclass(frozen=True)
class DeliveryAnnounced(Event):
    """The Spec was told its Tickets are delivered; `pull_request` is the draft pull request when there is one."""

    pull_request: str | None


@dataclass(frozen=True)
class SpecHandedToHuman(Event):
    """The Spec carries `label` and a comment saying why a person is needed."""

    label: str
    message: str


@dataclass(frozen=True)
class SpecBlocked(Event):
    """The Spec was blocked for a person; `labelled` is false when it had no work for the block to hold back."""

    label: str
    message: str
    labelled: bool


@dataclass(frozen=True)
class SpecFailureCommented(Event):
    """The Spec carries a comment that its run failed."""

    message: str

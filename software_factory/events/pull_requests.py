"""The pull request work item and the Events of watching, deciding on, publishing and finalizing a pull request."""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.agents import Json
from software_factory.events.feedback import FeedbackBatch
from software_factory.events.specs import RepositoryTarget
from software_factory.runtime import Event


@dataclass(frozen=True)
class PullRequestWork:
    """The Workflow context of one pull request: its repository target, number and the revision of what it is about."""

    target: RepositoryTarget
    number: int
    revision: str

    @property
    def id(self) -> str:
        """The work-item identity the ledger and spans use."""
        return f"{self.target.slug}#{self.number}"


@dataclass(frozen=True)
class PollStarted(Event):
    """Poll `number` began; durable only when its poll wrote something, so the highest one recorded bounds every poll id the ledger knows."""

    number: int


@dataclass(frozen=True)
class PullRequestPolled(Event):
    """One read of pull request `number` of `target`'s repository, as the snapshot dictionaries babysit's fingerprints and agent envelopes are built on; `login` is the acting account."""

    target: RepositoryTarget
    number: int
    snapshot: dict[str, Json]
    login: str = ""

    @property
    def is_open(self) -> bool:
        """The pull request is open."""
        return self.snapshot["pr"]["state"] == "open"  # type: ignore[index]

    @property
    def head(self) -> str:
        """The pull request's head commit."""
        return str(self.snapshot["pr"]["head"]["sha"])  # type: ignore[index]

    @property
    def branch(self) -> str:
        """The pull request's head branch."""
        return str(self.snapshot["pr"]["head"]["ref"])  # type: ignore[index]


@dataclass(frozen=True)
class DraftPullRequestPublished(Event):
    """`branch` is on origin with its draft pull request at `url`; `url` is None and `pushed` false when there was nothing to publish."""

    url: str | None
    branch: str
    pushed: bool


@dataclass(frozen=True)
class Finalized(Event):
    """The Spec's summary Comment is posted; `ready` says the pull request left draft, otherwise `blockers` say why it stays one."""

    ready: bool
    pull_request: str | None
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class OriginValidated(Event):
    """The checkout's origin is the pull request's repository `slug`."""

    slug: str


@dataclass(frozen=True)
class PullRequestClosed(Event):
    """The pull request is closed, `merged` or not, at `head`."""

    head: str
    merged: bool


@dataclass(frozen=True)
class Unsupported(Event):
    """The pull request cannot be maintained from this checkout; `reason` says why."""

    reason: str


@dataclass(frozen=True)
class CiFailing(Event):
    """CI failed on the pull request head `head`."""

    head: str


@dataclass(frozen=True)
class CopilotDue(Event):
    """A Copilot review is wanted for the pull request head `head` and was not requested yet."""

    head: str


@dataclass(frozen=True)
class ReadinessCheck(Event):
    """Nothing needs doing on `head`; `ready` says whether the pull request is ready to merge."""

    head: str
    ready: bool


type NextAction = PullRequestClosed | Unsupported | FeedbackBatch | CiFailing | CopilotDue | ReadinessCheck


@dataclass(frozen=True)
class CopilotRequested(Event):
    """A Copilot review was asked for on `head`; `outcome` is `requested`, or `unavailable` when GitHub refused the request."""

    head: str
    outcome: str


@dataclass(frozen=True)
class ReadinessReported(Event):
    """The readiness of `head` was reported as `ready`."""

    head: str
    ready: bool


@dataclass(frozen=True)
class PrBlocked(Event):
    """The pull request was blocked at `fingerprint`, the state of its review and CI inputs; `detail` is the reason an Event carried, empty when the route gave one."""

    fingerprint: str
    detail: str

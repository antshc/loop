"""The Events of publishing a branch and its draft pull request and of finalizing it."""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.agents import Json
from software_factory.events.specs import RepositoryTarget
from software_factory.runtime import Event


@dataclass(frozen=True)
class PullRequestPolled(Event):
    """One read of pull request `number` of `target`'s repository, as the snapshot dictionaries babysit's fingerprints and agent envelopes are built on."""

    target: RepositoryTarget
    number: int
    snapshot: dict[str, Json]

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

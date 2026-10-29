"""The Events of publishing a branch and its draft pull request and of finalizing it."""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.runtime import Event


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

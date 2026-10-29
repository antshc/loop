"""The Events of publishing a branch and its draft pull request."""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.runtime import Event


@dataclass(frozen=True)
class DraftPullRequestPublished(Event):
    """`branch` is on origin with its draft pull request at `url`; `url` is None and `pushed` false when there was nothing to publish."""

    url: str | None
    branch: str
    pushed: bool

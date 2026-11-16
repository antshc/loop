"""The address agent's request and report."""

from __future__ import annotations

from dataclasses import dataclass

from loop import Json, Message


@dataclass(frozen=True)
class AddressRequest(Message):
    """The pull request, its pinned head, the repository's checks and and the file holding the address gate's JSON the agent works from."""

    prUrl: str
    owner: str
    repo: str
    number: int
    headRef: str
    baseRef: str
    headOid: str
    buildCommand: tuple[str, ...]
    testCommand: tuple[str, ...]
    discussionFile: str


@dataclass(frozen=True)
class AddressReport(Message):
    """What the agent did: `outcome` is `addressed`, `waiting` or `blocked`; `head` is the commit it left the pull request at; `details` is its full report."""

    outcome: str
    head: str
    reason: str
    details: Json

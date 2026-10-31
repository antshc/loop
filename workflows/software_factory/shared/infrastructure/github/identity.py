"""How GitHub repositories, issues, and Comments are addressed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple


class RepositoryId(NamedTuple):
    """GitHub owner and repository name, represented as an owner/repo slug."""

    owner: str
    repo: str

    def __str__(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True)
class IssueRef:
    """An issue addressed by its repository and number."""

    repository: RepositoryId
    number: int

    def __str__(self) -> str:
        return f"{self.repository}#{self.number}"


@dataclass(frozen=True)
class IssueIdentity:
    """The identity GitHub keeps for an issue however its number or title changes: REST id and GraphQL node id."""

    id: int
    node_id: str


@dataclass(frozen=True)
class CommentRef:
    """A Comment addressed by its issue and its own REST id."""

    issue: IssueRef
    id: int

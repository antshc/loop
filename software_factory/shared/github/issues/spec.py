"""Specs and Tickets: issues with a delivery role, and the routing metadata a Spec carries."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Self

from .issue import Issue, IssueState


SPEC_LABEL = "spec"


HITL_LABEL = "hitl"


_BLOCKING_LABELS = frozenset({HITL_LABEL, SPEC_LABEL})


_INITIATIVE = re.compile(r"^(?P<initiative>[^:]+):\s*(?P<title>.+)$")


_TARGET_PREFIX = "repo:target:"


_BASE_PREFIX = "repo:base:"


_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class SpecMetadata:
    """What a Spec's title and routing labels say: its Initiative, target repository, and base branch."""

    initiative: str
    bare_title: str
    target: str | None
    base_branch: str | None

    @classmethod
    def of(cls, number: int, title: str, labels: tuple[str, ...]) -> SpecMetadata:
        """Reads the metadata; the Initiative falls back to the Spec number without a `<initiative>: ` title prefix."""
        match = _INITIATIVE.match(title)
        targets = [label[len(_TARGET_PREFIX) :] for label in labels if label.startswith(_TARGET_PREFIX)]
        bases = [label[len(_BASE_PREFIX) :] for label in labels if label.startswith(_BASE_PREFIX)]
        return cls(
            initiative=str(number) if match is None else match["initiative"].strip(),
            bare_title=title if match is None else match["title"].strip(),
            target=targets[0] if len(targets) == 1 and _SLUG.match(targets[0]) else None,
            base_branch=bases[0] if len(bases) == 1 and bases[0] else None,
        )

    @property
    def target_label(self) -> str | None:
        """The `repo:target:<owner/name>` label that routes the Spec, or None when there is no single valid target."""
        return None if self.target is None else f"{_TARGET_PREFIX}{self.target}"


def _narrowed(cls: type[Self], issue: Issue) -> Self:
    return cls(**{field.name: getattr(issue, field.name) for field in fields(Issue)})


@dataclass(frozen=True)
class Spec(Issue):
    """An issue labeled `spec`."""

    @classmethod
    def of(cls, issue: Issue) -> Spec:
        """The Spec view of `issue`."""
        return _narrowed(cls, issue)

    @property
    def metadata(self) -> SpecMetadata:
        """The Initiative, target repository, and base branch read from the title and routing labels."""
        return SpecMetadata.of(self.number, self.title, self.labels)


@dataclass(frozen=True)
class Ticket(Issue):
    """An open sub-issue of a Spec."""

    @classmethod
    def of(cls, issue: Issue) -> Ticket:
        """The Ticket view of `issue`."""
        return _narrowed(cls, issue)

    @property
    def actionable(self) -> bool:
        """Open and carrying neither the `hitl` nor the `spec` label."""
        return self.state == IssueState.OPEN and not ({label.casefold() for label in self.labels} & _BLOCKING_LABELS)

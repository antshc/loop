from dataclasses import dataclass, field
from typing import ClassVar

from .issue_comment import IssueComment


@dataclass
class Issue:
    number: int
    title: str
    body: str
    url: str
    labels: list[str] = field(default_factory=list)
    comments: list[IssueComment] = field(default_factory=list)

    _BLOCKING_LABELS: ClassVar[frozenset[str]] = frozenset({"hitl", "spec"})

    @property
    def is_actionable(self) -> bool:
        normalized_labels = {label.casefold() for label in self.labels}
        return not bool(normalized_labels & self._BLOCKING_LABELS)

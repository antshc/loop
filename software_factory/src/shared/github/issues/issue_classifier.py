"""Pure classification of an issue as a Spec from its `spec` label; the native type is only cross-checked."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .issue import Issue
from .spec import SPEC_LABEL


_SPEC_TYPE = "spec"


class IssueKind(StrEnum):
    """What role an issue plays on its own, before any Spec relation is read."""

    SPEC = "spec"
    ISSUE = "issue"


@dataclass(frozen=True)
class LabelTypeDisagreement:
    """The `spec` label and the native issue type point different ways."""

    has_spec_label: bool
    issue_type: str

    def __str__(self) -> str:
        label = "carries" if self.has_spec_label else "lacks"
        return f"issue {label} the `{SPEC_LABEL}` label but its native type is {self.issue_type!r}"


@dataclass(frozen=True)
class Classification:
    """The kind of an issue, and the label/type disagreement when there is one."""

    kind: IssueKind
    disagreement: LabelTypeDisagreement | None = None


class IssueClassifier:
    """Decides by label alone; a missing native type never blocks classification."""

    def classify(self, issue: Issue) -> Classification:
        """The `spec` label decides; a native type that says otherwise is reported, not obeyed."""
        has_label = SPEC_LABEL in {label.casefold() for label in issue.labels}
        kind = IssueKind.SPEC if has_label else IssueKind.ISSUE
        if issue.issue_type is None or (issue.issue_type.casefold() == _SPEC_TYPE) == has_label:
            return Classification(kind)
        return Classification(kind, LabelTypeDisagreement(has_label, issue.issue_type))

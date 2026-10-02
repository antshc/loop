from __future__ import annotations

from ..issue import Issue

ISSUE_KINDS = frozenset({"all", "implementation", "tests"})


class IssueFilter:
    """Filters Issue entities by actionability."""

    def get_actionable_issues(self, issues: list[Issue], *, kind: str = "all") -> list[Issue]:
        """Return approved issues for the requested work kind; all preserves launcher behavior."""
        if kind not in ISSUE_KINDS:
            raise ValueError(f"Unknown issue kind: {kind}")
        actionable = [issue for issue in issues if issue.is_actionable]
        if kind == "all":
            return actionable
        return [issue for issue in actionable
                if ("tests" in {label.casefold() for label in issue.labels}) == (kind == "tests")]

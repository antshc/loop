"""address_once's policy as values and pure functions: whose feedback is trusted and the commit subject of each fix."""

from __future__ import annotations

from typing import TYPE_CHECKING

from software_factory.events.feedback import TrustPolicy

if TYPE_CHECKING:
    from software_factory.stages.address import IssueWork

__all__ = ["TRUST", "address_subject"]

# The authors whose review feedback is acted on, the same rules pr_babysit applies; the acting account always is.
TRUST = TrustPolicy(
    associations=frozenset({"OWNER", "MEMBER", "COLLABORATOR"}),
    bots=frozenset({"copilot", "copilot-pull-request-reviewer", "chatgpt-codex-connector", "coderabbitai", "claude", "bugbot"}),
)


def address_subject(work: IssueWork) -> str:
    """The commit subject of the fix for one grouped review issue."""
    return f"address_once: address {work.issue.id} (PR #{work.polled.number})"

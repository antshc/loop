"""Immediate, ordered, retry-safe changes to issues: each command re-reads state first and skips what is already applied."""

from __future__ import annotations

import hashlib

from ..repo import GitHubRepo
from .errors import IncompleteReadError, IssueError
from .graph import SpecGraphSnapshot
from .issue import Comment, IssueState
from .reader import IssueReader
from .spec import Ticket


def _fingerprint(purpose: str, body: str) -> str:
    """A hidden marker stable for the Comment's purpose and content, so a retry can recognise it."""
    digest = hashlib.sha256(f"{purpose}\n{body}".encode()).hexdigest()[:16]
    return f"<!-- loop:comment {purpose}:{digest} -->"


def _ticket_marker(spec: SpecGraphSnapshot, key: str) -> str:
    """A hidden marker tying a Ticket body to its Spec and planned-Ticket key."""
    return f"<!-- loop:ticket spec={spec.number} key={key} -->"


class IssueCommands:
    """Changes one repository's issues through `gh`; each command takes effect at once and a failure raises where it happened."""

    def __init__(self, repo: GitHubRepo, reader: IssueReader) -> None:
        self._repo = repo
        self._reader = reader

    def comment(self, number: int, purpose: str, body: str) -> Comment:
        """Posts `body` on the issue, unless a Comment with the same purpose and content is already there; returns that Comment."""
        marker = _fingerprint(purpose, body)
        issue = self._reader.issue(number)
        for existing in issue.comments:
            if marker in existing.body:
                return existing
        if not issue.complete:
            raise IncompleteReadError(f"cannot tell whether {issue.ref} already has the {purpose!r} Comment: {issue.gaps}")
        node = self._repo.json(
            "api", "--method", "POST", f"repos/{self._repo.slug}/issues/{number}/comments", "-f", f"body={body}\n\n{marker}",
        )
        return self._reader.posted(issue.ref, node)

    def add_label(self, number: int, label: str) -> None:
        """Adds `label` to the issue, unless it already carries it."""
        if label not in self._reader.summary(number).labels:
            self._repo.run(("issue", "edit", str(number), "--repo", self._repo.slug, "--add-label", label))

    def remove_label(self, number: int, label: str) -> None:
        """Removes `label` from the issue, unless it does not carry it."""
        issue = self._reader.issue(number)
        # A label that is absent from a partly read issue may still be there.
        if label in issue.labels or any(gap.part == "labels" for gap in issue.gaps):
            self._repo.run(("issue", "edit", str(number), "--repo", self._repo.slug, "--remove-label", label))

    def close(self, number: int, comment: str) -> None:
        """Closes the issue with `comment`, unless it is already closed."""
        if self._reader.summary(number).state == IssueState.OPEN:
            self._repo.run(("issue", "close", str(number), "--repo", self._repo.slug, "--comment", comment))

    def find_ticket(self, spec: SpecGraphSnapshot, key: str) -> Ticket | None:
        """The open issue already created for `key` of `spec`, whether or not it is linked, else None."""
        marker = _ticket_marker(spec, key)
        for issue in self._reader.open_issues(self._routing_label(spec)):
            if marker in issue.body and issue.number != spec.number:
                return Ticket.of(issue)
        return None

    def create_ticket(self, spec: SpecGraphSnapshot, key: str, title: str, body: str) -> Ticket:
        """Creates an open issue for `key` of `spec`, marked so a retry finds it; it is not yet linked to `spec`."""
        label = self._routing_label(spec)
        marked = f"{body.rstrip()}\n\n{_ticket_marker(spec, key)}"
        node = self._repo.json(
            "api", "--method", "POST", f"repos/{self._repo.slug}/issues",
            "-f", f"title={title}", "-f", f"body={marked}", "-f", f"labels[]={label}",
        )
        return Ticket.of(self._reader.created(node))

    def link_ticket(self, spec: SpecGraphSnapshot, ticket: Ticket) -> None:
        """Makes `ticket` a sub-issue of `spec`, unless it already is one."""
        if ticket.number in self._reader.sub_issue_numbers(spec.number):
            return
        self._repo.run((
            "api", "--method", "POST", f"repos/{self._repo.slug}/issues/{spec.number}/sub_issues",
            "-F", f"sub_issue_id={ticket.identity.id}",
        ))

    def add_blocked_by(self, ticket: Ticket, blocker: Ticket) -> None:
        """Makes `ticket` wait for `blocker` as a native blocked-by dependency, unless it already does."""
        if blocker.ref in self._reader.blocker_refs(ticket.number):
            return
        self._repo.run((
            "api", "--method", "POST", f"repos/{self._repo.slug}/issues/{ticket.number}/dependencies/blocked_by",
            "-F", f"issue_id={blocker.identity.id}",
        ))

    @staticmethod
    def _routing_label(spec: SpecGraphSnapshot) -> str:
        label = spec.target_label
        if label is None:
            raise IssueError(f"cannot resolve the target repository of Spec {spec.url}")
        return label

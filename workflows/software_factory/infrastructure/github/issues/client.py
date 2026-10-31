"""One repository's issues: complete reads and immediate commands behind one client."""

from __future__ import annotations

from ..repo import GitHubRepo
from .commands import IssueCommands
from .errors import IssueError
from .graph import SpecGraphSnapshot
from .issue import Comment, Issue
from .reader import IssueReader
from .spec import Ticket


class Issues:
    """The issues of one repository reached through `gh`: Specs read with their Tickets, and the commands that change them."""

    def __init__(self, repo: GitHubRepo) -> None:
        self._reader = IssueReader(repo)
        self._commands = IssueCommands(repo, self._reader)

    def issue(self, number: int) -> Issue:
        """The issue with its labels and Comments."""
        return self._reader.issue(number)

    def specs(self, number: int | None = None) -> tuple[SpecGraphSnapshot, ...]:
        """Every open Spec with its Tickets and whether it was read completely; only Spec `number` when given."""
        return self._reader.specs(number)

    def spec(self, number: int) -> SpecGraphSnapshot:
        """The open Spec `number` with its Tickets; IssueError when it is not open."""
        found = self._reader.specs(number)
        if not found:
            raise IssueError(f"no open Spec #{number}")
        return found[0]

    def comment(self, number: int, purpose: str, body: str) -> Comment:
        """Posts `body` unless a Comment with the same purpose and content is already on the issue."""
        return self._commands.comment(number, purpose, body)

    def add_label(self, number: int, label: str) -> None:
        """Adds `label` unless the issue carries it."""
        self._commands.add_label(number, label)

    def remove_label(self, number: int, label: str) -> None:
        """Removes `label` unless the issue lacks it."""
        self._commands.remove_label(number, label)

    def close(self, number: int, comment: str) -> None:
        """Closes the issue with `comment` unless it is closed."""
        self._commands.close(number, comment)

    def find_tickets(self, spec: SpecGraphSnapshot) -> dict[str, Ticket]:
        """The open issues already created for `spec` by planned-Ticket key, linked or not."""
        return self._commands.find_tickets(spec)

    def create_ticket(
        self, spec: SpecGraphSnapshot, key: str, title: str, body: str
    ) -> Ticket:
        """Creates a marked, unlinked issue for `key` of `spec`."""
        return self._commands.create_ticket(spec, key, title, body)

    def sub_issue_numbers(self, spec: SpecGraphSnapshot) -> frozenset[int]:
        """The numbers of every sub-issue of `spec`, open or closed."""
        return self._commands.sub_issue_numbers(spec)

    def link_ticket(
        self,
        spec: SpecGraphSnapshot,
        ticket: Ticket,
        linked: frozenset[int] | None = None,
    ) -> None:
        """Makes `ticket` a sub-issue of `spec` unless it already is one; `linked` spares re-reading the sub-issues."""
        self._commands.link_ticket(spec, ticket, linked)

    def add_blocked_by(self, ticket: Ticket, blocker: Ticket) -> None:
        """Makes `ticket` wait for `blocker` as a native blocked-by dependency unless it already does."""
        self._commands.add_blocked_by(ticket, blocker)

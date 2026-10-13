"""The `ccode(<initiative-id>|<ticket-number>): ` delivering-commit subject convention."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentCommitSubject:
    """The response identifier and required commit-subject prefix for one Ticket."""

    initiative: str
    ticket_number: int

    def __str__(self) -> str:
        return f"{self.initiative}|{self.ticket_number}"

    @property
    def initiative_commit_prefix(self) -> str:
        """The prefix shared by all delivering commits for this Initiative."""
        return f"ccode({self.initiative}|"

    def to_subject(self) -> str:
        """The required prefix of a Ticket's delivering commit subject."""
        return f"ccode({self}): "

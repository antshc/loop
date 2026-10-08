"""The `ccode(<initiative-id>|<ticket-number>): ` delivering-commit subject convention."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkIdentifier:
    """The response envelope's `identifier`, and the commit subject's parenthesized tag."""

    initiative: str
    ticket_number: int

    def __str__(self) -> str:
        return f"{self.initiative}|{self.ticket_number}"

    def to_subject(self) -> str:
        """The required prefix of a Ticket's delivering commit subject."""
        return f"ccode({self}): "

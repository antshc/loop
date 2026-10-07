"""The `ccode(<initiative-id>|<ticket-number>): ` delivering-commit subject convention."""

from __future__ import annotations


def subject_prefix(identifier: str) -> str:
    """The required prefix of a Ticket's delivering commit subject."""
    return f"ccode({identifier}): "

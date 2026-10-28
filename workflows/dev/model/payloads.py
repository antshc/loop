"""The JSON dev hands its agents for a Spec, a Ticket, and a Comment; the shape is part of the agent prompts."""

from __future__ import annotations

from typing import Any

from workflows.shared.github.issues.graph import SpecGraphSnapshot
from workflows.shared.github.issues.issue import Comment
from workflows.shared.github.issues.spec import Ticket


def comment_payload(comment: Comment) -> dict[str, Any]:
    """A Comment as its author, body, and creation time."""
    return {"author": comment.author, "body": comment.body, "created_at": comment.created_at}


def ticket_payload(ticket: Ticket) -> dict[str, Any]:
    """A Ticket with its state, labels, body, and Comments."""
    return {
        "number": ticket.number,
        "title": ticket.title,
        "state": str(ticket.state),
        "labels": list(ticket.labels),
        "url": ticket.url,
        "body": ticket.body,
        "comments": [comment_payload(comment) for comment in ticket.comments],
    }


def spec_payload(spec: SpecGraphSnapshot) -> dict[str, Any]:
    """A Spec with its body and Comments."""
    return {
        "number": spec.number,
        "title": spec.title,
        "url": spec.url,
        "body": spec.body,
        "comments": [comment_payload(comment) for comment in spec.comments],
    }

"""The developer role: implements one Ticket and commits it."""

from __future__ import annotations

import json
from dataclasses import replace

from loop import DEFAULT, Role

from ... import _kit
from ...contract import Declined, ReplyMismatch, TypedAgent
from .messages import Delivered, DeveloperReply, DeveloperRequest

NO_COMMITS = "No task commits for this Initiative exist on the feature branch yet."


def _decode(request: DeveloperRequest, output: str) -> DeveloperReply:
    envelope = _kit.read_status(output, identifier=request.identifier)
    if envelope.failed:
        return envelope.declined()
    result = envelope.result
    commit, summary, verification = result.get("commit"), result.get("summary"), result.get("verification")
    if not isinstance(commit, str) or not isinstance(summary, str) or not isinstance(verification, str):
        raise ReplyMismatch(f"completed response is missing a required result field: {result!r}")
    return Delivered(commit, summary, verification)


DEVELOPER: TypedAgent[DeveloperRequest, DeveloperReply] = _kit.template_agent(
    "developer",
    package=__name__,
    profile=replace(DEFAULT, name=Role.DEVELOPER),
    args={
        "TICKET_JSON": lambda request: json.dumps(request.ticket, indent=2),
        "INITIATIVE_COMMITS": lambda request: "\n".join(request.initiative_commits) or NO_COMMITS,
        "COMMIT_SUBJECT_PREFIX": lambda request: request.commit_subject_prefix,
        "WORKTREE_PATH": lambda request: str(request.worktree),
        "TARGET_BRANCH": lambda request: request.base_branch,
        "FEATURE_BRANCH": lambda request: request.feature_branch,
    },
    decode=_decode,
)

__all__ = ["DEVELOPER", "Declined", "Delivered", "DeveloperReply", "DeveloperRequest"]

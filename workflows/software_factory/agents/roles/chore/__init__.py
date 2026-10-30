"""The chore role: refines a pull request's changes, leaving its edits uncommitted."""

from __future__ import annotations

from dataclasses import replace

from loop import DEFAULT

from ... import _kit
from loop import Declined, TypedAgent
from .messages import ChoreReply, ChoreRequest, Refined

NO_COMMITS = "No commits exist on the feature branch yet."


def _decode(request: ChoreRequest, output: str) -> ChoreReply:
    envelope = _kit.read_status(output)
    if envelope.failed:
        return envelope.declined()
    return Refined(
        envelope.text("commit_message"), envelope.text("summary"), tuple(str(item) for item in envelope.items("out_of_scope")),
    )


CHORE: TypedAgent[ChoreRequest, ChoreReply] = _kit.template_agent(
    "chore",
    package=__name__,
    profile=replace(DEFAULT, name="chore"),
    args={
        "PULL_REQUEST_URL": lambda request: request.pull_request_url,
        "WORKTREE_PATH": lambda request: str(request.worktree),
        "TARGET_BRANCH": lambda request: request.base_branch,
        "FEATURE_BRANCH": lambda request: request.feature_branch,
        "DIFF_DIR": lambda request: str(request.diff_dir),
        "BRANCH_COMMITS": lambda request: "\n\n".join(request.branch_commits) or NO_COMMITS,
    },
    decode=_decode,
)

__all__ = ["CHORE", "ChoreReply", "ChoreRequest", "Declined", "Refined"]

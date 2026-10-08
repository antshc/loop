"""The prompt template's arguments for one Ticket run."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from loop import Branch, CommitService
from workflows.platforms.work_tracking import Ticket, WorkIdentifier


def prompt_args(
    ticket: Ticket,
    identifier: WorkIdentifier,
    initiative_commits: Sequence[str],
    worktree: Path,
    base_branch: str,
    feature_branch: str,
) -> dict[str, str]:
    """Values for every placeholder in the dev prompt template."""
    return {
        "TICKET_JSON": json.dumps(asdict(ticket), indent=2),
        "INITIATIVE_COMMITS": "\n".join(initiative_commits) or "No task commits for this Initiative exist on the feature branch yet.",
        "TASK_ID": str(identifier),
        "COMMIT_SUBJECT_PREFIX": identifier.to_subject(),
        "WORKTREE_PATH": str(worktree),
        "TARGET_BRANCH": base_branch,
        "FEATURE_BRANCH": feature_branch,
    }


def initiative_commits(commits: CommitService, worktree: Path, base_branch: str, initiative: str) -> list[str]:
    """This Initiative's `ccode(<initiative-id>|` commits on the feature branch since the base branch, as `<short hash> <subject>`."""
    found = commits.find_since(Branch(worktree, base_branch), subject_prefix=f"ccode({initiative}|")
    return [f"{commit.sha[:7]} {commit.subject}" for commit in found]

"""The prompt template's arguments for one Ticket run."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from loop import Commit, GitClient
from workflows.platforms.work_tracking import Ticket


def prompt_args(
    ticket: Ticket,
    identifier: str,
    initiative_commits: Sequence[str],
    worktree: Path,
    base_branch: str,
    feature_branch: str,
) -> dict[str, str]:
    """Values for every placeholder in the dev prompt template."""
    return {
        "TICKET_JSON": json.dumps(asdict(ticket), indent=2),
        "INITIATIVE_COMMITS": "\n".join(initiative_commits) or "No task commits for this Initiative exist on the feature branch yet.",
        "TASK_ID": identifier,
        "COMMIT_SUBJECT_PREFIX": Commit.subject_prefix(identifier),
        "WORKTREE_PATH": str(worktree),
        "TARGET_BRANCH": base_branch,
        "FEATURE_BRANCH": feature_branch,
    }


def initiative_commits(git: GitClient, worktree: Path, base_branch: str, initiative: str) -> list[str]:
    """This Initiative's `ccode(<initiative-id>|` commits on the feature branch since the base branch."""
    return git.commits_with_prefix(worktree, f"{base_branch}..HEAD", f"ccode({initiative}|")

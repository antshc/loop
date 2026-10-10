"""The prompt template's arguments for one Ticket run, and their substitution."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from workflows.platforms.work_tracking import Ticket, WorkIdentifier

from .errors import PromptError

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")

logger = logging.getLogger("workflow.dev")


def render_prompt(template: str, args: Mapping[str, str]) -> str:
    """Replaces each `{{KEY}}` placeholder; a missing argument raises, an unused one is logged."""
    used: set[str] = set()

    def substitute(match: re.Match[str]) -> str:
        key = match[1]
        if key not in args:
            raise PromptError(f"missing prompt argument: {key}")
        used.add(key)
        return args[key]

    rendered = _PLACEHOLDER.sub(substitute, template)
    for key in sorted(set(args) - used):
        logger.warning("unused prompt argument: %s", key)
    return rendered


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

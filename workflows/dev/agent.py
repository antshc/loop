"""Named prompt templates and the default implementation agent delegate."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from pathlib import Path

from loop import DEFAULT, AgentRequest, AgentWorkspace, Role

from .records import AgentCommitSubject, Ticket, TicketContext, TicketResult as DevResult, TicketWorkflowError as DevError
from .reply import DevResponse


logger = logging.getLogger("workflow.dev")


PROMPTS = Path(__file__).parent / "prompts"
PROMPT = PROMPTS / "implement.md"


DEVELOPER = replace(DEFAULT, name=Role.DEVELOPER)


def load_prompt(name: str) -> str:
    """Reads a named Markdown template from the dev prompt folder."""
    return (PROMPTS / f"{name}.md").read_text()


class PromptError(DevError):
    """A prompt template placeholder has no matching argument."""


_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


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
    identifier: AgentCommitSubject,
    initiative_commits: Sequence[str],
    worktree: Path,
    base_branch: str,
    feature_branch: str,
) -> dict[str, str]:
    """Values for every placeholder in the dev prompt template."""
    return {
        "TICKET_JSON": json.dumps(asdict(ticket), indent=2),
        "INITIATIVE_COMMITS": "\n".join(initiative_commits) or "No task commits for this Initiative exist on the feature branch yet.",
        "COMMIT_SUBJECT_PREFIX": identifier.to_subject(),
        "WORKTREE_PATH": str(worktree),
        "TARGET_BRANCH": base_branch,
        "FEATURE_BRANCH": feature_branch,
    }


def implement_ticket(context: TicketContext, worktree: AgentWorkspace, *, template: str) -> DevResult:
    """Runs one fresh implementation agent and decodes its response for the selected Ticket."""
    args = prompt_args(
        context.ticket,
        context.identifier,
        context.initiative_commits,
        worktree.path,
        context.base_branch,
        context.feature_branch,
    )
    prompt = render_prompt(template, args)
    output = worktree.agent(DEVELOPER).run(AgentRequest(prompt)).output
    return DevResponse(context.identifier).decode(output)

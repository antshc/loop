"""The dev prompt folder and template rendering."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from workflows.shared.agents import prompts as shared

from ..model.records import TicketWorkflowError


logger = logging.getLogger("workflow.dev")


PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
PROMPT = PROMPTS / "implement.md"


class PromptError(TicketWorkflowError):
    """A prompt template placeholder has no matching argument."""


def load_prompt(name: str) -> str:
    """Reads a named Markdown template from the dev prompt folder."""
    return shared.PromptLibrary(PROMPTS).load(name)


def render_prompt(template: str, args: Mapping[str, str]) -> str:
    """Replaces each `{{KEY}}` placeholder; a missing argument raises, an unused one is logged."""
    try:
        return shared.render(template, args, log=logger)
    except shared.PromptError as exception:
        raise PromptError(str(exception)) from exception

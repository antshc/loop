"""A prompt template with the arguments that fill it."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["Prompt", "PromptError"]

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class PromptError(Exception):
    """A prompt template placeholder has no matching argument."""


@dataclass(frozen=True)
class Prompt:
    """A template plus the string arguments for its `{{KEY}}` placeholders."""

    template: str
    args: Mapping[str, str]

    def render(self) -> str:
        """Replaces each placeholder; a missing argument raises `PromptError`, an unused one is logged."""
        used: set[str] = set()

        def substitute(match: re.Match[str]) -> str:
            key = match[1]
            if key not in self.args:
                raise PromptError(f"missing prompt argument: {key}")
            used.add(key)
            return self.args[key]

        rendered = _PLACEHOLDER.sub(substitute, self.template)
        for key in sorted(set(self.args) - used):
            logger.warning("unused prompt argument: %s", key)
        return rendered

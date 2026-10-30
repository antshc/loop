"""`{{KEY}}` Markdown prompt templates."""

from __future__ import annotations

import re
from collections.abc import Mapping


_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class PromptError(ValueError):
    """A prompt template and its arguments do not match."""


def placeholders(template: str) -> set[str]:
    """Every placeholder name in `template`."""
    return set(_PLACEHOLDER.findall(template))


def render(template: str, args: Mapping[str, str]) -> str:
    """Replaces each `{{KEY}}` placeholder; a missing argument raises."""

    def substitute(match: re.Match[str]) -> str:
        key = match[1]
        if key not in args:
            raise PromptError(f"missing prompt argument: {key}")
        return args[key]

    return _PLACEHOLDER.sub(substitute, template)

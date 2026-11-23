"""`{{KEY}}` prompt templates."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .errors import PromptError

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def placeholders(template: str) -> set[str]:
    """Every placeholder name in `template`."""
    return set(_PLACEHOLDER.findall(template))


def render(template: str, args: Mapping[str, str]) -> str:
    """Replaces every placeholder; any unfilled one raises `PromptError`."""
    missing = placeholders(template) - set(args)
    if missing:
        raise PromptError(f"unfilled placeholders: {', '.join(sorted(missing))}")
    return _PLACEHOLDER.sub(lambda match: args[match[1]], template)

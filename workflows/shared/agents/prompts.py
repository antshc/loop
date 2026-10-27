"""Named Markdown prompt templates with `{{KEY}}` placeholders."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path


logger = logging.getLogger(__name__)


_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class PromptError(ValueError):
    """A prompt template placeholder has no matching argument."""


def placeholders(template: str) -> set[str]:
    """Every placeholder name in `template`."""
    return set(_PLACEHOLDER.findall(template))


def render(template: str, args: Mapping[str, str]) -> str:
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


class PromptLibrary:
    """Prompt templates named `<name>.md` in one folder."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, name: str) -> Path:
        return self.root / f"{name}.md"

    def load(self, name: str) -> str:
        """The raw template text."""
        return self.path(name).read_text(encoding="utf-8")

    def render(self, name: str, args: Mapping[str, str]) -> str:
        """The named template with every placeholder replaced."""
        return render(self.load(name), args)

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from loop.errors import PromptError

_MARK = "\x01"
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_COMMAND = re.compile(rf"{_MARK}!`([^`\n]+)`")

_logger = logging.getLogger("loop")


@dataclass(frozen=True)
class Prompt:
    """A prompt template with optional arguments for its placeholders; without args the template is sent verbatim."""

    template: str
    args: Mapping[str, str] | None = None

    def render(self, execute: Callable[[str], str]) -> str:
        """Replaces `{{KEY}}` placeholders, then expands template-authored !`cmd` through `execute`."""
        if self.args is None:
            return self.template
        values = {key: value.replace(_MARK, "") for key, value in self.args.items()}
        used: set[str] = set()

        def substitute(match: re.Match[str]) -> str:
            key = match[1]
            if key not in values:
                raise PromptError(f"missing prompt argument: {key}")
            used.add(key)
            return values[key]

        # Only commands present in the template are marked, so !`...` arriving through an argument never runs.
        marked = self.template.replace("!`", f"{_MARK}!`")
        substituted = _PLACEHOLDER.sub(substitute, marked)
        for key in sorted(set(self.args) - used):
            _logger.warning("unused prompt argument: %s", key)
        return _COMMAND.sub(lambda match: execute(match[1]).rstrip("\n"), substituted)

    def __str__(self) -> str:
        # Template-authored !`cmd` stay unexpanded: the agent client runs them, not this rendering.
        return self.render(lambda command: f"!`{command}`")

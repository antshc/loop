from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping

from orb.errors import PromptError

_MARK = "\x01"
_PLACEHOLDER = re.compile(r"\$\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_COMMAND = re.compile(rf"{_MARK}!`([^`\n]+)`")

_logger = logging.getLogger("orb")


class PromptPreprocessor:
    """Replaces `${{KEY}}` placeholders, then expands template-authored !`cmd` through `execute`."""

    def __init__(self, execute: Callable[[str], str]) -> None:
        self._execute = execute

    def process(self, prompt: str, prompt_args: Mapping[str, str] | None = None) -> str:
        args = prompt_args or {}
        values = {key: value.replace(_MARK, "") for key, value in args.items()}
        used: set[str] = set()

        def substitute(match: re.Match[str]) -> str:
            key = match[1]
            if key not in values:
                raise PromptError(f"missing prompt argument: {key}")
            used.add(key)
            return values[key]

        # Only commands present in the template are marked, so !`...` arriving through an argument never runs.
        marked = prompt.replace("!`", f"{_MARK}!`")
        substituted = _PLACEHOLDER.sub(substitute, marked)
        for key in sorted(set(args) - used):
            _logger.warning("unused prompt argument: %s", key)
        return _COMMAND.sub(lambda match: self._execute(match[1]).rstrip("\n"), substituted)

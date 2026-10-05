from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping

from orb.errors import PromptError

_MARK = "\x01"
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_COMMAND = re.compile(rf"{_MARK}!`([^`\n]+)`")

_logger = logging.getLogger("orb")


def render_prompt(
    template: str,
    args: Mapping[str, str],
    *,
    builtins: Mapping[str, str],
    execute: Callable[[str], str],
) -> str:
    """Substitute `{{KEY}}` placeholders, then run each template-authored !`cmd` through `execute`."""
    clashing = sorted(set(args) & set(builtins))
    if clashing:
        raise PromptError(f"prompt arguments override built-ins: {', '.join(clashing)}")
    values = {**builtins, **{key: value.replace(_MARK, "") for key, value in args.items()}}
    used: set[str] = set()

    def substitute(match: re.Match[str]) -> str:
        key = match[1]
        if key not in values:
            raise PromptError(f"missing prompt argument: {key}")
        used.add(key)
        return values[key]

    # Only commands present in the template are marked, so !`...` arriving through an argument never runs.
    marked = template.replace("!`", f"{_MARK}!`")
    substituted = _PLACEHOLDER.sub(substitute, marked)
    for key in sorted(set(args) - used):
        _logger.warning("unused prompt argument: %s", key)
    return _COMMAND.sub(lambda match: execute(match[1]).rstrip("\n"), substituted)

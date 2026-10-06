from __future__ import annotations

import json
import re
from typing import Any

from orb.errors import ExtractionError


def extract_tag(text: str, tag: str) -> str | None:
    """Return the trimmed body of the first `<tag>...</tag>` block, or None."""
    match = re.search(rf"<{re.escape(tag)}>(.*?)</{re.escape(tag)}>", text, re.DOTALL)
    return match[1].strip() if match else None


def extract_json(text: str, tag: str) -> Any:
    body = extract_tag(text, tag)
    if body is None:
        raise ExtractionError(f"no <{tag}> block in agent output:\n{text}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exception:
        raise ExtractionError(f"<{tag}> block is not valid JSON: {exception}") from exception

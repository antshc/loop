"""Extracts an agent's JSON response object from its raw output text."""

from __future__ import annotations

import json


def extract_response(text: str) -> str | None:
    """The JSON text of the last top-level object in `text` carrying a `status` key, or None."""
    decoder = json.JSONDecoder()
    found: str | None = None
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            index = text.find("{", index + 1)
            continue
        if isinstance(value, dict) and "status" in value:
            found = text[index:end]
            index = text.find("{", end)
        else:
            index = text.find("{", index + 1)
    return found

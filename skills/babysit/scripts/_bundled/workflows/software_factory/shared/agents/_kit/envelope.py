"""Extraction of the JSON response object an agent ends its output with."""

from __future__ import annotations

import json
from collections.abc import Callable

from loop import ReplyMismatch


class ContentDecodeError(ValueError):
    """`text` carried no value this decoder recognizes."""

    def __init__(self, message: str, output: str) -> None:
        super().__init__(message)
        self.output = output


class LastJsonObject:
    """The last top-level JSON object in the text satisfying `where`; raises when none does."""

    def __init__(self, where: Callable[[dict], bool] = lambda _: True) -> None:
        self._where = where

    def decode(self, text: str) -> dict:
        decoder = json.JSONDecoder()
        found: dict | None = None
        index = text.find("{")
        while index != -1:
            try:
                value, end = decoder.raw_decode(text, index)
            except json.JSONDecodeError:
                index = text.find("{", index + 1)
                continue
            if isinstance(value, dict) and self._where(value):
                found = value
                index = text.find("{", end)
            else:
                index = text.find("{", index + 1)
        if found is None:
            raise ContentDecodeError("no matching JSON object in text", text)
        return found


def read_object(output: str) -> dict:
    """The last top-level JSON object in `output`; a reply mismatch when there is none."""
    try:
        return LastJsonObject().decode(output)
    except ContentDecodeError as exception:
        raise ReplyMismatch("no response object in agent output") from exception

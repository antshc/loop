"""Content decoders used by workflow scripts to interpret agent output."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Protocol, TypeVar

T = TypeVar("T")


class ContentDecoder(Protocol[T]):
    """Decodes an agent's assistant text into `T`."""

    def decode(self, text: str) -> T: ...


class ContentDecodeError(Exception):
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

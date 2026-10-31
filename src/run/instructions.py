"""Load reusable instruction text without coupling it to an agent CLI."""

from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

_MAX_BYTES = 1024 * 1024


class InstructionsError(Exception):
    """An instruction source could not provide usable UTF-8 text."""


@dataclass(frozen=True)
class Instructions:
    """An inert file or public HTTP source resolved when an agent client is bound."""

    path: Path | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if (self.path is None) == (self.url is None):
            raise ValueError("instructions require exactly one file or URL")
        if self.path is not None and not self.path.is_absolute():
            raise ValueError("instruction file path must be absolute")
        if self.url is not None:
            parts = urlsplit(self.url)
            if (
                parts.scheme not in ("http", "https")
                or not parts.hostname
                or parts.username
                or parts.password
            ):
                raise ValueError(
                    "instructions require a public HTTP(S) URL without credentials"
                )

    @classmethod
    def from_file(cls, path: Path) -> Instructions:
        """Declare an absolute UTF-8 instruction file without reading it."""
        return cls(path=path)

    @classmethod
    def from_url(cls, url: str) -> Instructions:
        """Declare a public raw-text URL without fetching it."""
        return cls(url=url)


def resolve_instructions(
    source: str | Instructions | None, *, dry_run: bool = False
) -> str | None:
    """Snapshot instructions once, with no source I/O in dry-run mode."""
    if source is None:
        return None
    if isinstance(source, str):
        return _text(source)
    if dry_run:
        return f"[instructions from {source.path or source.url}; omitted in dry-run]"
    try:
        if source.path is not None:
            with source.path.open("rb") as stream:
                content = stream.read(_MAX_BYTES + 1)
        else:
            with urlopen(source.url, timeout=10) as stream:
                if "text/html" in stream.headers.get("Content-Type", "").lower():
                    raise InstructionsError(
                        "instruction URL must return raw text, not HTML"
                    )
                content = stream.read(_MAX_BYTES + 1)
        if len(content) > _MAX_BYTES:
            raise InstructionsError("instructions exceed the 1 MiB limit")
        return _text(content.decode("utf-8"))
    except (OSError, ValueError, HTTPException) as exception:
        raise InstructionsError("could not load UTF-8 instructions") from exception


def _text(text: str) -> str:
    if not text.strip():
        raise InstructionsError("instructions must not be empty")
    return text

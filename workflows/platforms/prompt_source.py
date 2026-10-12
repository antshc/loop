"""Where prompt templates are read from."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

__all__ = ["FilePromptSource", "PromptSource"]


class PromptSource(Protocol):
    """Supplies a prompt template's text by name."""

    def load(self, name: str) -> str: ...


class FilePromptSource:
    """Reads templates from `<root>/<name>.md`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def load(self, name: str) -> str:
        return (self._root / f"{name}.md").read_text()

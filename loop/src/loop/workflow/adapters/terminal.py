from __future__ import annotations

import sys
from typing import Any, TextIO

from ..phases import Phase
from .console import label


def require_rich() -> None:
    try:
        import rich  # noqa: F401
    except ImportError as exception:
        raise ImportError("Workflow visualization needs: pip install 'loop[visualization]'") from exception


class TerminalReporter:
    """Renders live phase occurrences on terminals and one final table when piped."""

    def __init__(self, name: str, definitions: tuple[Any, ...], stream: TextIO | None) -> None:
        from rich.console import Console
        self._name, self._definitions = name, definitions
        self._console = Console(file=stream if stream is not None else sys.stderr)
        self._rows: list[Phase] = []
        self._live: Any = None

    def emit(self, point: str, phase: Phase) -> None:
        from rich.live import Live
        if point == "started":
            self._rows.append(phase)
        root = phase.parent_id is None
        if root and point == "started" and self._console.is_terminal:
            self._live = Live(console=self._console, auto_refresh=False)
            self._live.start()
        try:
            if self._live is not None:
                self._live.update(self._table(), refresh=True)
            elif root and point == "finished":
                self._console.print(self._table())
        finally:
            if root and point == "finished" and self._live is not None:
                self._live.stop()
                self._live = None

    def _table(self) -> Any:
        from rich.table import Table
        from rich.text import Text
        table = Table(title=self._name)
        for column in ("Phase", "Owner", "Status", "Time"):
            table.add_column(column)
        seen = set()
        for phase in self._rows:
            seen.add(phase.name)
            if phase.visible:
                duration = "" if phase.status == "running" else f"{phase.seconds:.1f}s"
                table.add_row(Text(label(phase)), Text(phase.owner), phase.status, duration)
        for definition in self._definitions:
            if definition.visible and definition.name not in seen:
                table.add_row(Text(definition.label), Text(definition.owner), "pending", "")
        return table

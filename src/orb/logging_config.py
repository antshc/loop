from __future__ import annotations

import logging
from pathlib import Path

from pythonjsonlogger.json import JsonFormatter


def configure_logging(log_file: Path, level: str | int = "INFO") -> None:
    """Write structured JSON log records, one per line, to log_file at the given level."""
    numeric_level = level if isinstance(level, int) else getattr(logging, level.upper(), logging.INFO)

    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    handler.setLevel(numeric_level)

    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()
    root.addHandler(handler)

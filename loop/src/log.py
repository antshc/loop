from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TextIO

from pythonjsonlogger.json import JsonFormatter

DEFAULT_LOG_FILE = Path("loop.log")
_CONSOLE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(
    level: str | int = "INFO",
    *,
    log_file: Path | None = DEFAULT_LOG_FILE,
    stream: TextIO | None = None,
    logger: str = "loop",
) -> None:
    """Logs to the console at `level`; at DEBUG also writes JSON lines to `log_file` (None disables the file).

    Handlers go on the `logger` subtree only; pass "" to configure the root logger for a whole application.
    """
    numeric_level = level if isinstance(level, int) else getattr(logging, level.upper(), logging.INFO)

    handlers = [_console_handler(numeric_level, stream)]
    if numeric_level <= logging.DEBUG and log_file is not None:
        handlers.append(_file_handler(Path(log_file), numeric_level))

    # Replaces the target's handlers so repeated calls never duplicate output.
    target = logging.getLogger(logger)
    target.setLevel(numeric_level)
    target.handlers[:] = handlers
    # A named logger stops at its own handlers so a host's root handlers never print the same record twice.
    if logger:
        target.propagate = False


def _console_handler(level: int, stream: TextIO | None) -> logging.Handler:
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    handler.setLevel(level)
    return handler


def _file_handler(log_file: Path, level: int) -> logging.Handler:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    handler.setLevel(level)
    return handler

"""Central logging configuration for the AFK toolset.

Attaches two handlers to the root logger:
- File handler: NDJSON with ECS field names via python-json-logger
- Stderr handler: plain-text

AFK_LOG_LEVEL environment variable controls the log level (default: info).
"""

import datetime
import logging
import os
import sys
from pathlib import Path

from pythonjsonlogger.json import JsonFormatter


class _EcsJsonFormatter(JsonFormatter):
    """Rename stdlib fields to ECS names: levelname→level, name→logger."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        ct = datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc)
        if datefmt:
            return ct.strftime(datefmt)
        return ct.isoformat()

    def add_fields(self, log_record: dict, record: logging.LogRecord, message_dict: dict) -> None:
        super().add_fields(log_record, record, message_dict)
        log_record["level"] = log_record.pop("levelname", record.levelname)
        log_record["logger"] = log_record.pop("name", record.name)


def configure_logging(log_file: Path, level: str | None = None) -> None:
    """Configure the root logger with a JSON file handler and a plain stderr handler.

    Args:
        log_file: Path to the NDJSON log file. Parent directories are created
                  automatically.
        level:    Log level string (e.g. "info", "debug"). When *None* the value
                  of the ``AFK_LOG_LEVEL`` environment variable is used; if that
                  is also unset the level defaults to ``"info"``.
    """
    if level is None:
        level = os.environ.get("AFK_LOG_LEVEL", "info")

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove any handlers that were registered before this call so the
    # function is safe to call more than once in tests.
    root.handlers.clear()

    # --- file handler (JSON / NDJSON) ---
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    json_formatter = _EcsJsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "@timestamp"},
        datefmt="%Y-%m-%dT%H:%M:%S.%fZ",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(json_formatter)
    root.addHandler(file_handler)

    # --- stderr handler (plain text) ---
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(stderr_handler)

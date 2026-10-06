from __future__ import annotations

import json
import logging
from pathlib import Path

from orb import configure_logging


def _read_records(log_file: Path) -> list[dict]:
    return [json.loads(line) for line in log_file.read_text().splitlines() if line]


def test_configure_logging_writes_one_json_object_per_line_at_or_above_level(tmp_path: Path) -> None:
    log_file = tmp_path / "run.log"
    configure_logging(log_file, "WARNING")
    logger = logging.getLogger("orb.test.logging")

    logger.info("below threshold, not written")
    logger.warning("at threshold")
    logger.error("above threshold")

    records = _read_records(log_file)
    assert [record["message"] for record in records] == ["at threshold", "above threshold"]


def test_configure_logging_creates_the_log_files_parent_directory(tmp_path: Path) -> None:
    log_file = tmp_path / "nested" / "run.log"

    configure_logging(log_file)
    logging.getLogger("orb.test.logging.nested").info("hello")

    assert _read_records(log_file)[0]["message"] == "hello"

#!/usr/bin/env python3
"""Unit tests for configure_logging.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

import pytest

from afk.shared.logging import configure_logging


def _fresh_log_file() -> Path:
    """Return a path inside a fresh temporary directory."""
    tmp = Path(tempfile.mkdtemp())
    return tmp / "app.log"


def _last_json_line(log_file: Path) -> dict:
    lines = [l for l in log_file.read_text().splitlines() if l.strip()]
    return json.loads(lines[-1])


class TestConfigureLogging:
    """Feature: Configure Logging"""

    def teardown_method(self):
        # Reset root logger after each test to avoid handler pollution.
        root = logging.getLogger()
        for h in list(root.handlers):
            h.close()
            root.removeHandler(h)

    # ------------------------------------------------------------------
    # JSON file output
    # ------------------------------------------------------------------

    def test_file_emits_json_with_ecs_fields(self):
        # Scenario: File handler emits NDJSON with ECS field names
        log_file = _fresh_log_file()
        configure_logging(log_file, level="debug")

        logging.getLogger("test.logger").info("hello world")

        entry = _last_json_line(log_file)
        assert "@timestamp" in entry
        assert entry["level"] == "INFO"
        assert entry["logger"] == "test.logger"
        assert entry["message"] == "hello world"

    def test_file_each_line_is_valid_json(self):
        # Scenario: Every line written to the log file is a self-contained JSON object
        log_file = _fresh_log_file()
        configure_logging(log_file, level="debug")

        logger = logging.getLogger("test.multi")
        logger.info("line one")
        logger.warning("line two")
        logger.error("line three")

        for line in log_file.read_text().splitlines():
            json.loads(line)  # must not raise

    def test_timestamp_is_iso8601_utc(self):
        # Scenario: @timestamp is ISO 8601 UTC
        import re

        log_file = _fresh_log_file()
        configure_logging(log_file, level="debug")
        logging.getLogger("ts.test").info("ts check")

        entry = _last_json_line(log_file)
        # Expect format like 2024-01-15T12:34:56.123456Z
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", entry["@timestamp"])

    def test_stdlib_fields_not_present_under_old_names(self):
        # Scenario: levelname and name are remapped, not duplicated
        log_file = _fresh_log_file()
        configure_logging(log_file, level="debug")
        logging.getLogger("rename.test").info("rename")

        entry = _last_json_line(log_file)
        assert "levelname" not in entry
        assert "name" not in entry

    # ------------------------------------------------------------------
    # Stderr plain-text handler
    # ------------------------------------------------------------------

    def test_stderr_handler_emits_plain_text(self, capsys):
        # Scenario: Stderr handler emits plain text (not JSON)
        log_file = _fresh_log_file()
        configure_logging(log_file, level="info")
        logging.getLogger("stderr.test").info("plain text message")

        captured = capsys.readouterr()
        # Must not be parseable as JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(captured.err.strip().splitlines()[-1])

    # ------------------------------------------------------------------
    # Log level control
    # ------------------------------------------------------------------

    def test_explicit_level_controls_verbosity(self):
        # Scenario: Explicit level parameter is respected
        log_file = _fresh_log_file()
        configure_logging(log_file, level="warning")

        logger = logging.getLogger("level.test")
        logger.debug("should not appear")
        logger.info("also should not appear")
        logger.warning("should appear")

        lines = [l for l in log_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["message"] == "should appear"

    def test_afk_log_level_env_var_sets_level(self, monkeypatch):
        # Scenario: AFK_LOG_LEVEL controls level
        log_file = _fresh_log_file()
        monkeypatch.setenv("AFK_LOG_LEVEL", "warning")
        configure_logging(log_file)

        logger = logging.getLogger("env.test")
        logger.info("suppressed")
        logger.warning("visible")

        lines = [l for l in log_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["message"] == "visible"

    def test_unset_afk_log_level_defaults_to_info(self, monkeypatch):
        # Scenario: Unset AFK_LOG_LEVEL defaults to info
        log_file = _fresh_log_file()
        monkeypatch.delenv("AFK_LOG_LEVEL", raising=False)
        configure_logging(log_file)

        logger = logging.getLogger("default.test")
        logger.debug("debug suppressed")
        logger.info("info visible")

        lines = [l for l in log_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["message"] == "info visible"

    # ------------------------------------------------------------------
    # Directory creation
    # ------------------------------------------------------------------

    def test_creates_parent_directories(self):
        # Scenario: Parent directories for log_file are created automatically
        tmp = Path(tempfile.mkdtemp())
        nested = tmp / "a" / "b" / "c" / "app.log"
        configure_logging(nested, level="info")
        logging.getLogger("mkdir.test").info("created dirs")
        assert nested.exists()

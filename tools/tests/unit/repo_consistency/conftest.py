"""Fixtures for the repository consistency tests."""

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    """The real repository root, so checks can be run against its actual content."""
    return Path(__file__).resolve().parents[4]

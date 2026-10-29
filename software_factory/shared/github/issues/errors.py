"""Failures reading or changing issues."""

from __future__ import annotations


class IssueError(Exception):
    """An issue read or change could not be completed."""


class IncompleteReadError(IssueError):
    """A decision needed state that was not fully read."""

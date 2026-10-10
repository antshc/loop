"""Errors the dev workflow raises on purpose."""

from __future__ import annotations


class DevError(Exception):
    """Base class for every error the dev workflow raises on purpose."""


class PromptError(DevError):
    """A prompt template placeholder has no matching argument."""


class ExecutionStoreError(DevError):
    """The execution log on disk is unreadable."""

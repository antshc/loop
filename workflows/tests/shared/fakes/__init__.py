"""Fakes at the Workflow process boundaries, shared by every Workflow's tests."""

from __future__ import annotations

from .gh import PR_DIFF, FakeGhCli, ScriptedGh, issue

__all__ = ["PR_DIFF", "FakeGhCli", "ScriptedGh", "issue"]

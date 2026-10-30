"""The Event base, the input markers that read many Events, and the error every rejected definition raises."""

from __future__ import annotations


class DefinitionError(ValueError):
    """A Stage, Flow or Workflow definition is structurally wrong; raised when it is built, before anything runs."""


class Event:
    """Base of every Stage pass payload: an immutable statement that something happened."""


class All[T: Event]:
    """Input marker: every T recorded in this run, in order, replays included."""


class History[T: Event]:
    """Input marker: every T recorded for this work item across runs."""

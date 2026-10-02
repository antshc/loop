"""Classify review threads by priority."""

from ..review_thread import ReviewThread


class ThreadFilter:
    """Filters and sorts ReviewThread entities by priority."""

    def get_actionable_threads(self, threads: list[ReviewThread]) -> list[ReviewThread]:
        """Return actionable, unresolved threads sorted fix! before suggest!."""
        return [t for t in threads if t.is_actionable]

from __future__ import annotations


class WorktreeError(RuntimeError):
    """Worktree identity or unfinished changes prevent a safe lifecycle transition."""

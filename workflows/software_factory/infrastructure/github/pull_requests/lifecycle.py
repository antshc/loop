"""Whether closing or reopening a pull request does anything, is allowed, or is refused: a pure decision over a loaded snapshot."""

from __future__ import annotations

from enum import StrEnum

from .model import PullRequest


class LifecycleAction(StrEnum):
    """A change of a pull request's open or closed state."""

    CLOSE = "close"
    REOPEN = "reopen"


class LifecycleDecision(StrEnum):
    """VALID: the change applies. NO_OP: the pull request is already in the wanted state. INVALID: GitHub cannot make the change."""

    VALID = "valid"
    NO_OP = "no_op"
    INVALID = "invalid"


class PullRequestLifecycleError(Exception):
    """A requested change of a pull request's state is not possible."""


class PullRequestLifecyclePolicy:
    """Decides close and reopen from the pull request as read; it reads and changes nothing."""

    def decide(self, pull: PullRequest, action: LifecycleAction) -> LifecycleDecision:
        """What `action` does to `pull`: closing a closed one and reopening an open one are no-ops, and a merged one cannot be reopened."""
        if action is LifecycleAction.CLOSE:
            return LifecycleDecision.VALID if pull.is_open else LifecycleDecision.NO_OP
        if pull.is_open:
            return LifecycleDecision.NO_OP
        return LifecycleDecision.INVALID if pull.merged else LifecycleDecision.VALID

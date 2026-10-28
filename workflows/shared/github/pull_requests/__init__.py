"""GitHub pull requests: the typed read model, complete reads, and the client dev and pr_babysit use."""

from __future__ import annotations

from .client import PullRequests
from .model import PublishedPullRequest

__all__ = ["PublishedPullRequest", "PullRequests"]

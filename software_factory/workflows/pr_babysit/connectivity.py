"""Identify temporary GitHub read failures that can safely be retried."""

from __future__ import annotations


def github_unavailable(detail: str) -> bool:
    """Recognize temporary transport and service failures, not access refusals."""
    detail = detail.lower()
    return any(
        marker in detail
        for marker in (
            "error connecting to",
            "connection reset",
            "connection refused",
            "could not resolve host",
            "no such host",
            "network is unreachable",
            "timed out",
            "i/o timeout",
            "tls handshake timeout",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
        )
    )


def transient_account_read(reason: str) -> bool:
    """Only the failed read-only account lookup is safe to retry without a PR change."""
    command, _, detail = reason.partition("\n")
    return command.endswith(": gh api user") and github_unavailable(detail)

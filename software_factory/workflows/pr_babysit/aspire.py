"""Check whether the configured telemetry collector is reachable."""

from __future__ import annotations

import logging
import socket
from urllib.parse import urlsplit

logger = logging.getLogger("workflow.pr_babysit.aspire")


def reachable_endpoint(
    endpoint: str | None, timeout: float = 1.0, *, quiet: bool = False
) -> str | None:
    """`endpoint` when a collector accepts TCP connections there, else None with one warning unless `quiet`, so a missing collector does not flood the log with exporter retries."""
    if not endpoint:
        return None
    parts = urlsplit(endpoint)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((parts.hostname or "localhost", port), timeout):
            return endpoint
    except OSError as exception:
        if quiet:
            return None
        logger.warning(
            "OpenTelemetry export disabled: no collector at %s (%s)",
            endpoint,
            exception,
        )
        return None

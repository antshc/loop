"""Exports the process's Python logging records over OTLP/HTTP, so Aspire shows them beside the spans."""

from __future__ import annotations

import logging
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from urllib.parse import urlsplit

SERVICE_NAME = "loop-babysit"

logger = logging.getLogger("workflow.pr_babysit.log_export")


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


def _not_from_otel(record: logging.LogRecord) -> bool:
    """Keeps the exporter's own records out of the export, which would otherwise feed back into it."""
    return not record.name.startswith("opentelemetry")


@contextmanager
def exported_logs(endpoint: str | None) -> Iterator[None]:
    """Forwards root-logger records to `endpoint` (an OTLP base URL) while the block runs; a no-op without an endpoint, and an exporter that cannot be built only logs a warning."""
    if not endpoint:
        yield
        return
    try:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.resources import Resource

        # Same resource as the observer's main one, so Aspire lists logs and spans together.
        provider = LoggerProvider(
            resource=Resource.create(
                {"service.name": SERVICE_NAME, "service.instance.id": SERVICE_NAME}
            )
        )
        provider.add_log_record_processor(
            BatchLogRecordProcessor(
                OTLPLogExporter(endpoint=f"{endpoint.rstrip('/')}/v1/logs")
            )
        )
        handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
        handler.addFilter(_not_from_otel)
    except Exception as exception:
        logger.warning(
            "log export disabled: %s: %s", type(exception).__name__, exception
        )
        yield
        return
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        try:
            provider.shutdown()
        except Exception as exception:
            logger.warning(
                "could not flush logs: %s: %s", type(exception).__name__, exception
            )

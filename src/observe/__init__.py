from __future__ import annotations

from typing import TYPE_CHECKING

from .roles import Role
from .values import (
    AgentRunRecord,
    LogReading,
    RunObserver,
    ToolCall,
    Usage,
    WorktreeEvent,
)

if TYPE_CHECKING:
    from .otel import OtlpObserver

__all__ = [
    "AgentRunRecord",
    "LogReading",
    "Role",
    "RunObserver",
    "ToolCall",
    "Usage",
    "WorktreeEvent",
    "otlp_observer",
]


def otlp_observer(
    *,
    endpoint: str | None = None,
    service_name: str | None = None,
    capture_content: bool = False,
    resource_per_agent: bool = False,
) -> OtlpObserver:
    """An observer exporting spans and metrics over OTLP/HTTP; needs the `loop[otel]` extra."""
    try:
        from .otel import otlp_observer as build
    except ImportError as exception:
        raise ImportError(
            "otlp_observer needs the OpenTelemetry extra: pip install 'loop[otel]'"
        ) from exception
    return build(
        endpoint=endpoint,
        service_name=service_name,
        capture_content=capture_content,
        resource_per_agent=resource_per_agent,
    )

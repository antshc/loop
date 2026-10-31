from __future__ import annotations

import math
from collections.abc import Mapping
from types import MappingProxyType


def validate_timeout(timeout: float | None) -> None:
    """Reject process timeouts that cannot bound execution."""
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout_sec must be a finite positive number")


def freeze_env(env: Mapping[str, str] | None) -> Mapping[str, str] | None:
    """Copy and validate environment overrides without retaining mutable caller state."""
    if env is None:
        return None
    for key, value in env.items():
        if not isinstance(key, str) or not key or "=" in key or "\0" in key:
            raise ValueError(
                "environment keys must be nonempty strings without '=' or NUL"
            )
        if not isinstance(value, str) or "\0" in value:
            raise ValueError("environment values must be strings without NUL")
    return MappingProxyType(dict(env))

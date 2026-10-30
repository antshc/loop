from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionName:
    """Caller-chosen name of a session; one name is an independent session per CLI."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or any(char.isspace() for char in self.value):
            raise ValueError(f"invalid session name: {self.value!r}")

    @classmethod
    def new(cls) -> SessionName:
        return cls(f"loop-{uuid.uuid4().hex}")


@dataclass(frozen=True)
class NativeHandle:
    """The id one CLI resumes a session by; only adapters read `value`."""

    cli: str
    value: str


@dataclass(frozen=True)
class Start:
    """Turn that starts a session under `name`."""

    name: SessionName


@dataclass(frozen=True)
class Resume:
    """Turn that continues the session behind `handle`."""

    name: SessionName
    handle: NativeHandle


Turn = Start | Resume


@dataclass(frozen=True)
class CliOutcome:
    """Parsed CLI output plus the handle to resume by."""

    output: str
    handle: NativeHandle
    exit_code: int


class SessionHandleMissing(Exception):
    """The CLI output carried no handle to resume by."""


class SessionCliMismatch(Exception):
    """A stored handle belongs to a different CLI than the one running."""

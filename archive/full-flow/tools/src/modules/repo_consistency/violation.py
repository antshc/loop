"""Shared result type for the repository consistency checks."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Violation:
    """One consistency failure, naming the offending file and the problem."""

    file: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}: {self.message}"

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkItem:
    id: str
    title: str
    state: str
    tags: tuple[str, ...]
    url: str


class WorkTracker(ABC):
    @abstractmethod
    def list_specs(self) -> list[WorkItem]: ...

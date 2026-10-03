from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRunResult:
    success: bool
    output: str


class AgentClient(ABC):
    @abstractmethod
    def run(self, prompt: str) -> AgentRunResult: ...

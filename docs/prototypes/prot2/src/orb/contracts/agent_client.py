from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from orb.contracts.sandbox import SandboxHandle


@dataclass(frozen=True)
class AgentResult:
    success: bool
    output: str


class AgentClient(ABC):
    @abstractmethod
    def run(self, prompt: str, sandbox: SandboxHandle) -> AgentResult: ...

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from ..run import AgentContext


class DockerService(Protocol):
    def configure(self, context: AgentContext) -> AgentContext: ...


class DockerRuntime:
    def configure(self, context: AgentContext) -> AgentContext:
        # Demonstration only: records intent; runner does not launch Docker.
        return replace(context, docker_image="agent:latest")

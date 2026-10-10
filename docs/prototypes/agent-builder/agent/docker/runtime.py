from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from ..run import RunContext


class DockerService(Protocol):
    def configure(self, context: RunContext) -> RunContext: ...


class DockerRuntime:
    def configure(self, context: RunContext) -> RunContext:
        # Demonstration only: records intent; runner does not launch Docker.
        return replace(context, agent=replace(context.agent, docker_image="agent:latest"))

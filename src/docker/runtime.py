from __future__ import annotations

import logging
from dataclasses import replace
from typing import Protocol

from ..run import AgentContext

logger = logging.getLogger(__name__)


class DockerService(Protocol):
    def configure(self, context: AgentContext) -> AgentContext: ...


class DockerRuntime:
    def configure(self, context: AgentContext) -> AgentContext:
        # Demonstration only: records intent; runner does not launch Docker.
        logger.debug("docker image: agent:latest")
        return replace(context, docker_image="agent:latest")

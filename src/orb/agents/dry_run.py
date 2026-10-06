from __future__ import annotations

import logging

from orb.contracts.agent_client import AgentClient, AgentOptions, AgentResult, AgentSession, SessionStore
from orb.contracts.capsule import AgentClientFactory
from orb.process import CommandExecutor, checked_output
from orb.prompt import PromptPreprocessor

logger = logging.getLogger("orb.agents.dry_run")


class DryRunAgentClient(AgentClient):
    """Renders and logs the prompt and returns success without ever starting a provider process."""

    def _invoke(
        self,
        prompt: str,
        options: AgentOptions,
        session: AgentSession | None,
        *,
        resume: bool,
    ) -> AgentResult:
        logger.info(prompt)
        return AgentResult("", "", 0)


def dry_run(sessions: SessionStore) -> AgentClientFactory:
    """A factory a capsule calls with its own executor; the executor is only used for template commands."""

    def create(executor: CommandExecutor) -> DryRunAgentClient:
        def execute(command: str) -> str:
            return checked_output(command, executor(command))

        return DryRunAgentClient(executor, PromptPreprocessor(execute), sessions)

    return create

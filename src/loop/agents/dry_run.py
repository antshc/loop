from __future__ import annotations

import logging

from loop.contracts.agent_client import AgentClient, AgentOptions, AgentResult, AgentSession, SessionStore
from loop.contracts.sandbox import AgentClientFactory, SandboxBinding
from loop.process import checked_output
from loop.prompt import PromptPreprocessor

logger = logging.getLogger("loop.agents.dry_run")


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
    """A factory a sandbox calls with its own binding; the executor is only used for template commands."""

    def create(binding: SandboxBinding) -> DryRunAgentClient:
        def execute(command: str) -> str:
            return checked_output(command, binding.executor(command))

        return DryRunAgentClient(binding.executor, PromptPreprocessor(execute), sessions)

    return create

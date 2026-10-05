from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping

from orb.contracts.agent_client import (
    AgentClient,
    AgentOptions,
    AgentResult,
    AgentSession,
    SessionStore,
)
from orb.contracts.capsule import CommandExecutor
from orb.prompt import PromptPreprocessor


class AgentClientBase(AgentClient):
    """Renders the prompt, resolves the logical session, and leaves command building to the provider."""

    def __init__(
        self,
        executor: CommandExecutor,
        preprocessor: PromptPreprocessor,
        sessions: SessionStore,
    ) -> None:
        self._executor = executor
        self._preprocessor = preprocessor
        self._sessions = sessions

    def run(
        self,
        prompt: str,
        prompt_args: Mapping[str, str] | None = None,
        options: AgentOptions | None = None,
    ) -> AgentResult:
        options = options or AgentOptions()
        text = self._preprocessor.process(prompt, prompt_args)
        if options.session_key is None:
            return self._invoke(text, options, None, resume=False)

        session = self._sessions.get(options.session_key)
        if session is not None:
            return self._invoke(text, options, session, resume=True)

        session = AgentSession(
            key=options.session_key,
            name=f"{options.session_name_prefix}{options.session_key}",
        )
        result = self._invoke(text, options, session, resume=False)
        # A failed create may leave no provider session to resume.
        if result.success:
            self._sessions.save(session)
        return result

    @abstractmethod
    def _invoke(
        self,
        prompt: str,
        options: AgentOptions,
        session: AgentSession | None,
        *,
        resume: bool,
    ) -> AgentResult: ...

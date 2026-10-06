from __future__ import annotations

import json
import logging

from orb.contracts.agent_client import (
    DEFAULT_COMPLETION_SIGNAL,
    AgentClient,
    AgentOptions,
    AgentResult,
    AgentSession,
    SessionStore,
)
from orb.contracts.capsule import AgentClientFactory
from orb.process import CommandExecutor, checked_output
from orb.prompt import PromptPreprocessor

logger = logging.getLogger("orb.agents.copilot")


class CopilotClient(AgentClient):
    """Runs one prompt through `copilot -p`, parsing its JSON event stream for the assistant's text."""

    def __init__(
        self,
        executor: CommandExecutor,
        preprocessor: PromptPreprocessor,
        sessions: SessionStore,
        *,
        executable: str = "copilot",
        completion_signal: str = DEFAULT_COMPLETION_SIGNAL,
    ) -> None:
        super().__init__(executor, preprocessor, sessions)
        self._executable = executable
        self._completion_signal = completion_signal

    def _invoke(
        self,
        prompt: str,
        options: AgentOptions,
        session: AgentSession | None,
        *,
        resume: bool,
    ) -> AgentResult:
        args = [self._executable, "-p", prompt, "--output-format", "json", *options.extra_args]
        if session is not None:
            args += [f"--resume={session.name}"] if resume else ["--name", session.name]
        if options.model:
            args += ["--model", options.model]
        if options.agent:
            args += ["--agent", options.agent]
        for directory in options.add_dirs:
            args += ["--add-dir", str(directory)]

        text_parts: list[str] = []
        completed = False

        def on_line(line: str) -> bool:
            nonlocal completed
            logger.info(line)
            event = _parse_event(line)
            if event is not None and event.get("type") == "assistant.message_delta":
                delta = event.get("data", {}).get("deltaContent", "")
                if delta:
                    text_parts.append(delta)
            if not completed and self._completion_signal in "".join(text_parts):
                completed = True
                return True
            return False

        result = self._executor(args, timeout_s=options.timeout_s, on_line=on_line)
        return AgentResult("".join(text_parts), result.stderr, result.returncode, completed=completed)


def _parse_event(line: str) -> dict | None:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def copilot(sessions: SessionStore, *, executable: str = "copilot") -> AgentClientFactory:
    """A factory a capsule calls with its own executor, so the CLI runs where the capsule runs."""

    def create(executor: CommandExecutor) -> CopilotClient:
        def execute(command: str) -> str:
            return checked_output(command, executor(command))

        return CopilotClient(executor, PromptPreprocessor(execute), sessions, executable=executable)

    return create


from __future__ import annotations

import json
import logging

from loop.contracts.agent_client import (
    AgentBinding,
    AgentClient,
    AgentClientFactory,
    AgentOptions,
    AgentOutputParser,
    AgentResult,
    AgentSession,
    SessionStore,
)
from loop.process import CommandExecutor, checked_output
from loop.prompt import PromptPreprocessor

logger = logging.getLogger("loop.agents.copilot")


class CopilotOutputParser(AgentOutputParser):
    """The last `{identifier, status, result}` object in Copilot's concatenated assistant text is the response."""

    def parse(self, stdout: str, stderr: str, exit_code: int) -> AgentResult:
        envelope = _last_envelope(stdout)
        response = envelope[0] if envelope is not None else ""
        success = exit_code == 0 and envelope is not None and envelope[1].get("status") == "completed"
        return AgentResult(stdout, stderr, exit_code, response=response, success=success)


class CopilotClient(AgentClient):
    """Runs one prompt through `copilot -p`, parsing its JSON event stream for the assistant's text."""

    def __init__(
        self,
        executor: CommandExecutor,
        preprocessor: PromptPreprocessor,
        sessions: SessionStore,
        *,
        workspace: str,
        executable: str = "copilot",
        parser: AgentOutputParser | None = None,
    ) -> None:
        super().__init__(executor, preprocessor, sessions)
        self._workspace = workspace
        self._executable = executable
        self._parser = parser or CopilotOutputParser()

    def _invoke(
        self,
        prompt: str,
        options: AgentOptions,
        session: AgentSession | None,
        *,
        resume: bool,
    ) -> AgentResult:
        args = [self._executable, "-p", prompt, "--output-format", "json"]
        args += ["--allow-all-tools", "--add-dir", self._workspace]
        args += list(options.extra_args)
        if session is not None:
            args += [f"--resume={session.name}"] if resume else ["--name", session.name]
        if options.model:
            args += ["--model", options.model]
        if options.agent:
            args += ["--agent", options.agent]
        for directory in options.add_dirs:
            args += ["--add-dir", str(directory)]
        for tool in options.deny_tools:
            args += ["--deny-tool", tool]

        text_parts: list[str] = []

        # Logs and collects output only; the process always runs until it exits or times out.
        def on_line(line: str) -> None:
            logger.info(line)
            event = _parse_event(line)
            if event is not None and event.get("type") == "assistant.message_delta":
                delta = event.get("data", {}).get("deltaContent", "")
                if delta:
                    text_parts.append(delta)

        result = self._executor(args, timeout_s=options.timeout_s, on_line=on_line)
        return self._parser.parse("".join(text_parts), result.stderr, result.returncode)


def _parse_event(line: str) -> dict | None:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _json_objects(text: str) -> list[tuple[str, dict]]:
    """Every balanced top-level `{...}` substring of `text` that parses as a JSON object."""
    objects: list[tuple[str, dict]] = []
    depth = 0
    start = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                raw = text[start : index + 1]
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    objects.append((raw, parsed))
    return objects


def _last_envelope(text: str) -> tuple[str, dict] | None:
    """The last top-level JSON object in `text` carrying both `identifier` and `status`, if any."""
    candidates = [item for item in _json_objects(text) if "identifier" in item[1] and "status" in item[1]]
    return candidates[-1] if candidates else None


def copilot(sessions: SessionStore, *, executable: str = "copilot") -> AgentClientFactory:
    """A factory a run calls with its binding, so the CLI runs through the run's executor."""

    def create(binding: AgentBinding) -> CopilotClient:
        def execute(command: str) -> str:
            return checked_output(command, binding.executor(command))

        return CopilotClient(
            binding.executor,
            PromptPreprocessor(execute),
            sessions,
            workspace=binding.workspace,
            executable=executable,
        )

    return create


from __future__ import annotations

from orb.agents.base import AgentClientBase
from orb.contracts.agent_client import AgentOptions, AgentResult, AgentSession, SessionStore
from orb.contracts.capsule import AgentClientFactory, CommandExecutor
from orb.process import checked_output
from orb.prompt import PromptPreprocessor


class CopilotClient(AgentClientBase):
    """Runs one prompt through `copilot -p` using the capsule's executor."""

    def __init__(
        self,
        executor: CommandExecutor,
        preprocessor: PromptPreprocessor,
        sessions: SessionStore,
        *,
        executable: str = "copilot",
    ) -> None:
        super().__init__(executor, preprocessor, sessions)
        self._executable = executable

    def _invoke(
        self,
        prompt: str,
        options: AgentOptions,
        session: AgentSession | None,
        *,
        resume: bool,
    ) -> AgentResult:
        args = [self._executable, "-p", prompt, "--silent", *options.extra_args]
        if session is not None:
            args += [f"--resume={session.name}"] if resume else ["--name", session.name]
        if options.model:
            args += ["--model", options.model]
        for directory in options.add_dirs:
            args += ["--add-dir", str(directory)]
        result = self._executor(args, timeout_s=options.timeout_s)
        return AgentResult(result.stdout, result.stderr, result.returncode)


def copilot(sessions: SessionStore, *, executable: str = "copilot") -> AgentClientFactory:
    """A factory a capsule calls with its own executor, so the CLI runs where the capsule runs."""

    def create(executor: CommandExecutor) -> CopilotClient:
        def execute(command: str) -> str:
            return checked_output(command, executor(command))

        return CopilotClient(executor, PromptPreprocessor(execute), sessions, executable=executable)

    return create

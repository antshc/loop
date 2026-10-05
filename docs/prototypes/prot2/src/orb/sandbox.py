from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from orb.contracts.agent_client import AgentClient
from orb.contracts.sandbox import Hooks, SandboxInstance, SandboxProvider
from orb.errors import AgentError, CommandError, PromptError
from orb.prompt import render_prompt

DEFAULT_COMPLETION_SIGNAL = "<promise>COMPLETE</promise>"

_logger = logging.getLogger("orb")


@dataclass(frozen=True)
class RunResult:
    stdout: str
    commits: tuple[str, ...]
    branch: str
    iterations: int
    completed: bool


class Sandbox:
    """A reusable sandbox: several agent runs share one branch and working tree."""

    def __init__(self, instance: SandboxInstance) -> None:
        self._instance = instance

    @property
    def path(self) -> Path:
        return self._instance.path

    @property
    def branch(self) -> str:
        return self._instance.branch

    def run(
        self,
        *,
        agent: AgentClient,
        name: str = "agent",
        prompt: str | None = None,
        prompt_file: Path | str | None = None,
        prompt_args: Mapping[str, str] | None = None,
        max_iterations: int = 1,
        completion_signal: str | None = DEFAULT_COMPLETION_SIGNAL,
    ) -> RunResult:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if (prompt is None) == (prompt_file is None):
            raise PromptError("pass exactly one of prompt or prompt_file")
        if prompt is not None and prompt_args:
            raise PromptError("prompt_args need a prompt_file; inline prompts are not templated")
        template = Path(prompt_file).read_text() if prompt_file is not None else None

        base = self._instance.head()
        stdout = ""
        completed = False
        iteration = 0
        for iteration in range(1, max_iterations + 1):
            _logger.info("[%s] iteration %d/%d on %s", name, iteration, max_iterations, self.branch)
            text = prompt if template is None else render_prompt(
                template,
                prompt_args or {},
                builtins={
                    "SOURCE_BRANCH": self._instance.branch,
                    "TARGET_BRANCH": self._instance.host_branch,
                },
                execute=self._instance.exec,
            )
            result = agent.run(text, self._instance.path)
            if not result.success:
                raise AgentError(name, result.output)
            stdout = result.output
            if completion_signal and completion_signal in stdout:
                completed = True
                break
        commits = self._instance.commits_since(base)
        return RunResult(stdout, commits, self.branch, iteration, completed)

    def merge_into_host(self) -> None:
        self._instance.merge_into_host()

    def close(self) -> None:
        self._instance.close()

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def create_sandbox(
    *,
    sandbox: SandboxProvider,
    repo: Path | None = None,
    branch: str | None = None,
    hooks: Hooks | None = None,
) -> Sandbox:
    instance = sandbox.open((repo or Path.cwd()).resolve(), branch)
    try:
        for hook in (hooks or Hooks()).on_sandbox_ready:
            instance.exec(hook.command, timeout_s=hook.timeout_s)
    except CommandError:
        instance.close()
        raise
    return Sandbox(instance)

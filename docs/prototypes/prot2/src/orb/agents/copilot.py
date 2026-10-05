from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from orb.contracts.agent_client import AgentClient, AgentResult
from orb.process import execute


class CopilotCliAgent(AgentClient):
    """Runs one prompt through `copilot -p` inside the capsule directory."""

    def __init__(
        self,
        model: str | None = None,
        *,
        executable: str = "copilot",
        add_dirs: Sequence[Path] = (),
        extra_args: Sequence[str] = ("--allow-all-tools", "--no-color"),
        timeout_s: float | None = None,
    ) -> None:
        self._model = model
        self._executable = executable
        self._add_dirs = tuple(add_dirs)
        self._extra_args = tuple(extra_args)
        self._timeout_s = timeout_s

    def run(self, prompt: str, cwd: Path) -> AgentResult:
        args = [self._executable, "-p", prompt, "--silent", *self._extra_args]
        if self._model:
            args += ["--model", self._model]
        for directory in self._add_dirs:
            args += ["--add-dir", str(directory)]
        result = execute(args, cwd=cwd, timeout_s=self._timeout_s)
        if result.returncode == 0:
            return AgentResult(success=True, output=result.stdout)
        return AgentResult(success=False, output=result.stdout + result.stderr)


def copilot(
    model: str | None = None,
    *,
    add_dirs: Sequence[Path] = (),
    timeout_s: float | None = None,
) -> CopilotCliAgent:
    return CopilotCliAgent(model, add_dirs=add_dirs, timeout_s=timeout_s)

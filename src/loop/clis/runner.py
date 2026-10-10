from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Protocol

from ..hooks import AgentCliHookWiring
from ..run import AgentRequest, RunContext
from ..sessions import CliOutcome, Turn
from .base import AgentProfile


class CliRunner(Protocol):
    def run(self, profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext) -> CliOutcome: ...


class ProcessCliRunner:
    """Runs any AgentCli as a process in the agent cwd; `run` is injectable for testing."""

    def __init__(self, *, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self._run = run

    def run(self, profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext) -> CliOutcome:
        argv = profile.cli.command(request, profile, turn, context)
        wiring = (
            profile.cli.hook_wiring(context.agent_cli_hooks, turn, context.agent.cwd)
            if context.agent_cli_hooks
            else AgentCliHookWiring()
        )
        extra = {"env": {**os.environ, **wiring.env}} if wiring.env else {}
        with self._hook_files(wiring, context.agent.cwd):
            result = self._run(argv, cwd=context.agent.cwd, check=True, capture_output=True, text=True, **extra)
        return profile.cli.parse(result.stdout, turn, result.returncode)

    @contextmanager
    def _hook_files(self, wiring: AgentCliHookWiring, cwd: Path) -> Iterator[None]:
        written: list[Path] = []
        try:
            for relative, content in wiring.files.items():
                path = cwd / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                written.append(path)
            if wiring.git_excludes:
                self._exclude(cwd, wiring.git_excludes)
            yield
        finally:
            for path in written:
                path.unlink(missing_ok=True)

    def _exclude(self, cwd: Path, patterns: tuple[str, ...]) -> None:
        found = self._run(
            ["git", "-C", str(cwd), "rev-parse", "--git-path", "info/exclude"],
            check=False, capture_output=True, text=True,
        )
        if found.returncode != 0 or not found.stdout.strip():
            return
        exclude = cwd / found.stdout.strip()
        existing = exclude.read_text().splitlines() if exclude.exists() else []
        missing = [pattern for pattern in patterns if pattern not in existing]
        if missing:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a") as handle:
                handle.writelines(f"{pattern}\n" for pattern in missing)

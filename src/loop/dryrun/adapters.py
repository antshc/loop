from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from ..clis import AgentCli, AgentProfile
from ..docker import DockerService
from ..git import GitCli
from ..hooks import LoopHook, ordered
from ..run import AgentContext, AgentRequest, RunContext
from ..sessions import CliOutcome, NativeHandle, Resume, Turn


def _log(message: str) -> None:
    print(f"[dry-run] {message}")


class LoggingGitCli(GitCli):
    """Dry-run GitCli: logs each git command instead of running it; show-ref reports a missing ref."""

    def _invoke(
        self, repository: Path, args: tuple[str, ...], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(repository), *args]
        _log(" ".join(command))
        return subprocess.CompletedProcess(command, 1 if "show-ref" in args else 0, "", "")

    def run_hook(self, hook: LoopHook, cwd: Path, repository: Path, worktree: Path) -> None:
        _log(f"loop-hook {hook.point.value} cwd={cwd} timeout={hook.timeout_sec}: {hook.command}")


class LoggingRunner:
    """Dry-run CliRunner: logs the command instead of running it."""

    def run(self, profile: AgentProfile, request: AgentRequest, turn: Turn, context: RunContext) -> CliOutcome:
        command = profile.cli.command(request, profile, turn, context)
        output = f"cwd={context.agent.cwd} cmd={' '.join(command)}"
        _log(output)
        self._log_hooks(profile, turn, context)
        return CliOutcome(output, self._handle(profile.cli, turn), 0)

    def _log_hooks(self, profile: AgentProfile, turn: Turn, context: RunContext) -> None:
        if not context.agent_cli_hooks:
            return
        cli = profile.cli
        for point, hook in ordered(context.agent_cli_hooks):
            _log(f"hook {cli.name} {point.value} -> {cli.native_point(point)}: {hook.command}")
        wiring = cli.hook_wiring(context.agent_cli_hooks, turn, context.agent.cwd)
        for path in wiring.files:
            _log(f"hook file {context.agent.cwd / path}")
        if wiring.args:
            _log(f"hook args {' '.join(wiring.args)}")
        if wiring.env:
            _log(f"hook env {dict(wiring.env)}")

    def _handle(self, cli: AgentCli, turn: Turn) -> NativeHandle:
        if isinstance(turn, Resume):
            return turn.handle
        return cli.handle_for_new(turn.name) or NativeHandle(cli.name, f"dry-{uuid.uuid4().hex}")


class LoggingDocker:
    """Dry-run DockerService: delegates to the stub and logs the configured image."""

    def __init__(self, inner: DockerService) -> None:
        self._inner = inner

    def configure(self, context: AgentContext) -> AgentContext:
        configured = self._inner.configure(context)
        _log(f"docker image={configured.docker_image}")
        return configured

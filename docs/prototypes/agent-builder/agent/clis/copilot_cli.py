from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..hooks import AgentCliHook, AgentCliHookPoint, AgentCliHookWiring, ordered, shim_command
from ..run import AgentRequest, RunContext
from ..sessions import CliOutcome, NativeHandle, Resume, SessionName, Start, Turn
from .base import AgentProfile


class CopilotCli:
    """Copilot CLI adapter."""

    name = "copilot"
    hook_points = frozenset(AgentCliHookPoint)
    _EVENTS = {
        AgentCliHookPoint.SESSION_START: "sessionStart",
        AgentCliHookPoint.SESSION_END: "sessionEnd",
        AgentCliHookPoint.AGENT_STOP: "agentStop",
        AgentCliHookPoint.PROMPT_SUBMITTED: "userPromptSubmitted",
        AgentCliHookPoint.PRE_TOOL: "preToolUse",
        AgentCliHookPoint.POST_TOOL: "postToolUse",
        AgentCliHookPoint.SUBAGENT_START: "subagentStart",
        AgentCliHookPoint.SUBAGENT_STOP: "subagentStop",
        AgentCliHookPoint.PRE_COMPACT: "preCompact",
    }

    def __init__(self, args: tuple[str, ...] = ("--allow-all-tools",)) -> None:
        self._args = args

    def native_point(self, point: AgentCliHookPoint) -> str:
        return self._EVENTS[point]

    def hook_wiring(self, hooks: tuple[AgentCliHook, ...], turn: Turn, workdir: Path) -> AgentCliHookWiring:
        events: dict[str, list[dict[str, object]]] = {}
        for point, hook in ordered(hooks):
            events.setdefault(self._EVENTS[point], []).append(
                {"type": "command", "bash": shim_command(self.name, point, turn, hook), "timeoutSec": hook.timeout_sec}
            )
        digest = hashlib.sha1(turn.name.value.encode()).hexdigest()[:8]
        content = json.dumps({"version": 1, "hooks": events}, indent=2)
        return AgentCliHookWiring(
            files={Path(f".github/hooks/loop-{digest}.json"): content},
            git_excludes=(".github/hooks/loop-*.json",),
            # Without it `copilot -p` silently skips repo hook files in an untrusted worktree.
            env={"GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS": "true"},
        )

    def handle_for_new(self, name: SessionName) -> NativeHandle:
        return NativeHandle(self.name, name.value)

    def command(
        self, request: AgentRequest, profile: AgentProfile, turn: Turn, context: RunContext
    ) -> list[str]:
        dirs = [part for directory in context.agent.add_dirs for part in ("--add-dir", str(directory))]
        settings = {"--model": profile.model, "--reasoning-effort": profile.reasoning_effort, "--context": profile.context}
        run_args = [part for flag, value in settings.items() if value is not None for part in (flag, value)]
        session_args = ["--name", turn.name.value] if isinstance(turn, Start) else [f"--resume={turn.handle.value}"]
        return ["copilot", "-p", request.prompt, *session_args, *self._args, *run_args, *profile.args, *dirs]

    def parse(self, stdout: str, turn: Turn, exit_code: int) -> CliOutcome:
        handle = turn.handle if isinstance(turn, Resume) else self.handle_for_new(turn.name)
        return CliOutcome(stdout, handle, exit_code)

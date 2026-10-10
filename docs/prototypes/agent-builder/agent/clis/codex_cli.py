from __future__ import annotations

import json
from pathlib import Path

from ..hooks import AgentCliHook, AgentCliHookPoint, AgentCliHookWiring, ordered, shim_command
from ..run import AgentRequest, RunContext
from ..sessions import CliOutcome, NativeHandle, Resume, SessionHandleMissing, SessionName, Turn
from .base import AgentProfile


def _parse_codex_events(stdout: str) -> tuple[str | None, str]:
    """Return (thread id, last agent message) from `codex exec --json` JSONL output."""
    thread_id: str | None = None
    message = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        elif event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message":
            message = event["item"].get("text", "")
    return thread_id, message


class CodexCli:
    """Codex CLI adapter; Codex assigns its own thread id."""

    name = "codex"
    _EVENTS = {
        AgentCliHookPoint.SESSION_START: "SessionStart",
        AgentCliHookPoint.SESSION_END: "SessionEnd",
        AgentCliHookPoint.AGENT_STOP: "Stop",
        AgentCliHookPoint.PROMPT_SUBMITTED: "UserPromptSubmit",
        AgentCliHookPoint.PRE_TOOL: "PreToolUse",
        AgentCliHookPoint.POST_TOOL: "PostToolUse",
        AgentCliHookPoint.SUBAGENT_START: "SubagentStart",
        AgentCliHookPoint.SUBAGENT_STOP: "SubagentStop",
        AgentCliHookPoint.PRE_COMPACT: "PreCompact",
    }
    _SESSION_END_MAX_TIMEOUT = 3

    def __init__(self, args: tuple[str, ...] = ("--sandbox", "workspace-write"), *, hook_trust_bypass: bool = False) -> None:
        self._args = args
        self._hook_trust_bypass = hook_trust_bypass
        # Without the bypass Codex silently skips the hooks, so none are claimed.
        self.hook_points = frozenset(AgentCliHookPoint) if hook_trust_bypass else frozenset()

    def native_point(self, point: AgentCliHookPoint) -> str:
        return self._EVENTS[point]

    def hook_wiring(self, hooks: tuple[AgentCliHook, ...], turn: Turn, workdir: Path) -> AgentCliHookWiring:
        events: dict[str, list[str]] = {}
        for point, hook in ordered(hooks):
            timeout = hook.timeout_sec
            if point is AgentCliHookPoint.SESSION_END:
                timeout = min(timeout, self._SESSION_END_MAX_TIMEOUT)
            command = json.dumps(shim_command(self.name, point, turn, hook))
            matcher = 'matcher="startup|resume",' if point is AgentCliHookPoint.SESSION_START else ""
            events.setdefault(self._EVENTS[point], []).append(
                f'{{{matcher}hooks=[{{type="command",command={command},timeout={timeout}}}]}}'
            )
        args: list[str] = []
        for event, entries in events.items():
            args += ["-c", f"hooks.{event}=[{','.join(entries)}]"]
        return AgentCliHookWiring(args=(*args, "--dangerously-bypass-hook-trust"))

    def handle_for_new(self, name: SessionName) -> None:
        return None

    def command(
        self, request: AgentRequest, profile: AgentProfile, turn: Turn, context: RunContext
    ) -> list[str]:
        dirs = [part for directory in context.agent.add_dirs for part in ("--add-dir", str(directory))]
        run_args: list[str] = []
        if profile.model is not None:
            run_args += ["--model", profile.model]
        if profile.reasoning_effort is not None:
            run_args += ["-c", f"model_reasoning_effort={profile.reasoning_effort}"]
        head = ["codex", "exec", "--json", *self._args, *run_args, *profile.args, *dirs]
        if context.agent_cli_hooks:
            head += self.hook_wiring(context.agent_cli_hooks, turn, context.agent.cwd).args
        if isinstance(turn, Resume):
            return [*head[:2], "resume", turn.handle.value, *head[2:], request.prompt]
        return [*head, request.prompt]

    def parse(self, stdout: str, turn: Turn, exit_code: int) -> CliOutcome:
        thread_id, message = _parse_codex_events(stdout)
        if thread_id is not None:
            return CliOutcome(message, NativeHandle(self.name, thread_id), exit_code)
        if isinstance(turn, Resume):
            return CliOutcome(message, turn.handle, exit_code)
        raise SessionHandleMissing("codex output had no thread.started event")

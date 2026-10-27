"""One agent request whose answer is a JSON envelope."""

from __future__ import annotations

import json

from loop import AgentProfile, AgentRequest, AgentWorkspace

from workflows.shared.agents.envelope import ContentDecodeError, LastJsonObject

from ..model import Blocked

_ENVELOPE = LastJsonObject()


def ask(workspace: AgentWorkspace, profile: AgentProfile, mode: str, payload: dict) -> dict:
    """Runs one agent request and returns its JSON envelope; Blocked when the agent fails or answers without one."""
    result = workspace.agent(profile).run(AgentRequest(json.dumps({"mode": mode, **payload})))
    if result.exit_code:
        raise Blocked(f"{profile.name} exited {result.exit_code}")
    try:
        return _ENVELOPE.decode(result.output)
    except ContentDecodeError as exception:
        raise Blocked(f"{profile.name} returned invalid JSON") from exception

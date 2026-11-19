"""The address role: runs the `review` skill's `address` procedure on a pull request's review threads inside babysit's worktree, owning commits, the push, replies and thread resolution."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

from loop import AgentProfile, ReplyMismatch, TypedAgent, copilot
from loop.software_factory import agents as agent_tools

from software_factory.src.pull_requests.infrastructure.github.pull_requests import (
    discussion_state,
)

from .messages import AddressReport, AddressRequest

_OUTCOMES = ("addressed", "waiting", "blocked")
_HERE = Path(__file__).resolve().parent
_GATE_COMMAND = shlex.join(
    (sys.executable, str(Path(discussion_state.__file__).resolve()))
)

_TEMPLATE = (_HERE / "instructions.md").read_text(encoding="utf-8")

_ARGS = frozenset(
    {
        "prUrl",
        "owner",
        "repo",
        "number",
        "headRef",
        "baseRef",
        "headOid",
        "buildCommand",
        "testCommand",
        "gateCommand",
        "discussionFile",
    }
)
if agent_tools.placeholders(_TEMPLATE) != _ARGS:
    raise agent_tools.PromptError(
        "address instructions placeholders and arguments differ: "
        + ", ".join(sorted(agent_tools.placeholders(_TEMPLATE) ^ _ARGS))
    )


def render(request: AddressRequest) -> str:
    """The address instructions with the request's values and discussion file path in their placeholders."""
    args = {
        "prUrl": request.prUrl,
        "owner": request.owner,
        "repo": request.repo,
        "number": str(request.number),
        "headRef": request.headRef,
        "baseRef": request.baseRef,
        "headOid": request.headOid,
        "buildCommand": json.dumps(list(request.buildCommand)),
        "testCommand": json.dumps(list(request.testCommand)),
        "gateCommand": _GATE_COMMAND,
        "discussionFile": request.discussionFile,
    }
    return agent_tools.render(_TEMPLATE, args)


def _decode(request: AddressRequest, output: str) -> AddressReport:
    result: dict[str, Any] = agent_tools.read_object(output)
    outcome, head, reason = (
        result.get("outcome"),
        result.get("head", ""),
        result.get("reason", ""),
    )
    if outcome not in _OUTCOMES:
        raise ReplyMismatch(f"invalid outcome: {outcome!r}")
    if not isinstance(head, str) or not isinstance(reason, str):
        raise ReplyMismatch(f"head and reason must be text: {result!r}")
    return AddressReport(outcome, head, reason, result)


# Default Copilot permissions (`--allow-all`): the agent commits, pushes and posts through `gh` itself.
_PROFILE = AgentProfile(copilot, name="address", timeout_sec=1800)

ADDRESS: TypedAgent[AddressRequest, AddressReport] = TypedAgent(
    "address", _PROFILE, render, _decode
)

__all__ = ["ADDRESS", "AddressReport", "AddressRequest", "render"]

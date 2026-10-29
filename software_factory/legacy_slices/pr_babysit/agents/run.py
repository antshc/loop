"""Runs a role agent for one PR, turning agent failures into the Blocked outcome."""

from __future__ import annotations

from loop import AgentWorkspace

from software_factory.agents import AgentError, Message, TypedAgent

from ..model import Blocked


def run_agent[Q: Message, R](agent: TypedAgent[Q, R], workspace: AgentWorkspace, request: Q) -> R:
    """Runs one role agent in the PR's recovery worktree; Blocked when it fails or answers unusably."""
    try:
        return agent.run(workspace, request)
    except AgentError as exception:
        raise Blocked(str(exception)) from exception

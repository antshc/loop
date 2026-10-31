"""Resolves where a CLI process starts and what it is told, per `AgentStartMode`."""

from __future__ import annotations

import shlex
from dataclasses import replace
from pathlib import Path

from .context import AgentContext, AgentStartMode
from .request import AgentRequest

__all__ = ["apply_start_mode", "directory_instruction"]


def directory_instruction(worktree: Path) -> str:
    quoted = shlex.quote(str(worktree))
    return (
        "This session was started in the harness repository only to load its context. "
        f"All work on the target code happens in the git worktree at: {worktree}\n"
        f"Before you inspect, edit, build, test or commit anything, change into it (`cd {quoted}`). "
        "Shell state does not persist between tool calls, so run every command from that directory "
        f"(start it with `cd {quoted} &&`) or give it as the working directory. "
        "Keep using the harness context, but never treat the harness checkout as the target code "
        "and make no changes outside the worktree."
    )


def apply_start_mode(
    agent: AgentContext, request: AgentRequest
) -> tuple[AgentContext, AgentRequest]:
    """In harness mode: launch in the harness, grant access to the worktree, and prefix the prompt with the cd instruction."""
    if agent.start_mode is AgentStartMode.WORKTREE:
        return agent, request
    if agent.harness_dir is None:
        raise ValueError("start_mode 'harness' requires harness_dir")
    worktree = agent.cwd
    add_dirs = (
        agent.add_dirs if worktree in agent.add_dirs else (*agent.add_dirs, worktree)
    )
    launched = replace(agent, cwd=agent.harness_dir, add_dirs=add_dirs)
    return launched, replace(
        request, prompt=f"{directory_instruction(worktree)}\n\n{request.prompt}"
    )

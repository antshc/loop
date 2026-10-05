from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from orb.contracts.agent_client import AgentClient
from orb.contracts.sandbox import Hooks, SandboxProvider
from orb.sandbox import DEFAULT_COMPLETION_SIGNAL, RunResult, create_sandbox


def run(
    *,
    sandbox: SandboxProvider,
    agent: AgentClient,
    repo: Path | None = None,
    branch: str | None = None,
    hooks: Hooks | None = None,
    merge_to_host: bool = True,
    name: str = "agent",
    prompt: str | None = None,
    prompt_file: Path | str | None = None,
    prompt_args: Mapping[str, str] | None = None,
    max_iterations: int = 1,
    completion_signal: str | None = DEFAULT_COMPLETION_SIGNAL,
) -> RunResult:
    """One-shot run in a fresh sandbox; commits on an unnamed branch merge into the host branch."""
    with create_sandbox(sandbox=sandbox, repo=repo, branch=branch, hooks=hooks) as box:
        result = box.run(
            agent=agent,
            name=name,
            prompt=prompt,
            prompt_file=prompt_file,
            prompt_args=prompt_args,
            max_iterations=max_iterations,
            completion_signal=completion_signal,
        )
        if branch is None and merge_to_host and result.commits:
            box.merge_into_host()
        return result

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from uuid import uuid4

from orb.contracts.agent_client import AgentOptions, AgentResult
from orb.contracts.capsule import AgentClientFactory, Capsule
from orb.errors import Cancelled
from orb.platforms.git_client import GitClient
from orb.sandbox.sandbox_lifecycle import SandboxHooks, run_host_hooks, with_sandbox_lifecycle

CapsuleFactory = Callable[[Path, threading.Event], Capsule]


@dataclass(frozen=True)
class SandboxRunResult:
    result: AgentResult
    branch: str
    commits: tuple[str, ...]


class Sandbox:
    """A long-lived worktree and Capsule; each `run` is wrapped in the sandbox lifecycle."""

    def __init__(
        self,
        git: GitClient,
        capsule: Capsule,
        checkout: Path,
        worktree: Path,
        branch: str,
        *,
        merge_to_head: bool,
        apply_to_host: Callable[[], None] | None,
        cancel: threading.Event,
    ) -> None:
        self._git = git
        self._capsule = capsule
        self._checkout = checkout
        self._worktree = worktree
        self._branch = branch
        self._merge_to_head = merge_to_head
        self._apply_to_host = apply_to_host
        self._cancel = cancel
        self._closed = False

    @property
    def worktree(self) -> Path:
        return self._worktree

    @property
    def branch(self) -> str:
        return self._branch

    def run(
        self,
        agent: AgentClientFactory,
        prompt: str,
        prompt_args: Mapping[str, str] | None = None,
        options: AgentOptions | None = None,
    ) -> SandboxRunResult:
        outcome = with_sandbox_lifecycle(
            self._git,
            self._capsule,
            self._checkout,
            self._worktree,
            lambda _base_head: self._capsule.run(agent, prompt, prompt_args, options),
            branch=None if self._merge_to_head else self._branch,
            apply_to_host=self._apply_to_host,
            keep_source_branch=self._merge_to_head,
            cancel=self._cancel,
        )
        return SandboxRunResult(outcome.result, outcome.branch, outcome.commits)

    def close(self, *, keep_worktree: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._capsule.close()
        finally:
            if not keep_worktree:
                self._git.remove_worktree(self._worktree)

    def __enter__(self) -> Sandbox:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # A cancelled run with uncommitted work keeps its worktree for the user.
        self.close(keep_worktree=isinstance(exception, Cancelled) and self._git.has_changes(self._worktree))


def create_sandbox(
    git: GitClient,
    capsule_factory: CapsuleFactory,
    *,
    checkout: Path,
    harness_root: Path,
    base: str,
    branch: str | None = None,
    hooks: SandboxHooks = SandboxHooks(),
    merge_to_head: bool = False,
    apply_to_host: Callable[[], None] | None = None,
    cancel: threading.Event | None = None,
) -> Sandbox:
    """Create the worktree, start the Capsule, and run the setup hooks.

    `merge_to_head` merges each run's commits into the checkout's current branch and keeps the
    worktree on its branch. A branch named `orb/sandbox-<id>` is generated when none is given.
    """
    cancel = cancel or threading.Event()
    branch = branch or f"orb/sandbox-{uuid4().hex[:8]}"
    worktree = git.create_worktree(
        checkout, branch, base, harness_root, on_ready=hooks.worktree_ready, cancel=cancel
    )
    try:
        capsule = capsule_factory(harness_root, cancel)
        try:
            run_host_hooks(git, hooks.sandbox_ready, worktree, cancel=cancel)
        except Exception:
            capsule.close()
            raise
    except Cancelled as exception:
        if git.has_changes(worktree):
            raise Cancelled(worktree) from exception
        git.remove_worktree(worktree)
        raise
    except Exception:
        git.remove_worktree(worktree)
        raise
    return Sandbox(
        git,
        capsule,
        checkout,
        worktree,
        branch,
        merge_to_head=merge_to_head,
        apply_to_host=apply_to_host,
        cancel=cancel,
    )

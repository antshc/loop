from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from loop.contracts.agent_client import AgentOptions, AgentResult
from loop.contracts.sandbox import AgentClientFactory, Sandbox
from loop.errors import Cancelled
from loop.platforms.git_client import GitClient
from loop.platforms.worktree_service import WorktreeService
from loop.sandboxes.sandbox_lifecycle import SandboxHooks, run_host_hooks, with_sandbox_lifecycle

SandboxFactory = Callable[[Path, threading.Event], Sandbox]


@dataclass(frozen=True)
class SandboxRunResult:
    result: AgentResult
    branch: str
    commits: tuple[str, ...]


class WorktreeSandbox:
    """A long-lived worktree and Sandbox; each `run` is wrapped in the sandbox lifecycle."""

    def __init__(
        self,
        git: GitClient,
        worktrees: WorktreeService,
        sandbox: Sandbox,
        checkout: Path,
        worktree: Path,
        branch: str,
        *,
        merge_to_head: bool,
        apply_to_host: Callable[[], None] | None,
        cancel: threading.Event,
    ) -> None:
        self._git = git
        self._worktrees = worktrees
        self._sandbox = sandbox
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
            self._sandbox,
            self._checkout,
            self._worktree,
            lambda _base_head: self._sandbox.run(agent, prompt, prompt_args, options),
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
            self._sandbox.close()
        finally:
            if not keep_worktree:
                self._worktrees.remove(self._worktree)

    def __enter__(self) -> WorktreeSandbox:
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
    worktrees: WorktreeService,
    sandbox_factory: SandboxFactory,
    *,
    checkout: Path,
    harness_root: Path,
    base: str,
    branch: str | None = None,
    hooks: SandboxHooks = SandboxHooks(),
    merge_to_head: bool = False,
    apply_to_host: Callable[[], None] | None = None,
    cancel: threading.Event | None = None,
) -> WorktreeSandbox:
    """Create the worktree, start the Sandbox, and run the setup hooks.

    `merge_to_head` merges each run's commits into the checkout's current branch and keeps the
    worktree on its branch. A branch named `loop/sandbox-<id>` is generated when none is given.
    """
    cancel = cancel or threading.Event()
    branch = branch or worktrees.new_branch_name()
    worktree = worktrees.create(checkout, branch, base, harness_root)
    try:
        run_host_hooks(git, hooks.worktree_ready, worktree, cancel=cancel)
        sandbox = sandbox_factory(harness_root, cancel)
        try:
            run_host_hooks(git, hooks.sandbox_ready, worktree, cancel=cancel)
        except Exception:
            sandbox.close()
            raise
    except Cancelled as exception:
        if git.has_changes(worktree):
            raise Cancelled(worktree) from exception
        worktrees.remove(worktree)
        raise
    except Exception:
        worktrees.remove(worktree)
        raise
    return WorktreeSandbox(
        git,
        worktrees,
        sandbox,
        checkout,
        worktree,
        branch,
        merge_to_head=merge_to_head,
        apply_to_host=apply_to_host,
        cancel=cancel,
    )

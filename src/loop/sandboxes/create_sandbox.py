from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from loop.contracts.agent_client import AgentOptions, AgentResult
from loop.contracts.sandbox import AgentClientFactory, Sandbox
from loop.errors import Cancelled
from loop.platforms.git import Branch, Git, Worktree
from loop.sandboxes.sandbox_lifecycle import SandboxHooks, run_host_hooks, with_sandbox_lifecycle

SandboxFactory = Callable[[Path, threading.Event], Sandbox]


def _default_worktree_path(checkout: Path, harness_root: Path, branch: str) -> Path:
    return harness_root / "workspace" / f"{checkout.name}.worktrees" / branch


def _exclude_workspace(harness_root: Path) -> None:
    """Adds `workspace/` to the harness checkout's local exclude list, once."""
    exclude_file = harness_root / ".git" / "info" / "exclude"
    exclude_file.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_file.read_text() if exclude_file.exists() else ""
    if "workspace/" in existing.splitlines():
        return
    with exclude_file.open("a") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write("workspace/\n")


@dataclass(frozen=True)
class SandboxRunResult:
    result: AgentResult
    branch: str
    commits: tuple[str, ...]


class WorktreeSandbox:
    """A long-lived worktree and Sandbox; each `run` is wrapped in the sandbox lifecycle."""

    def __init__(
        self,
        git: Git,
        sandbox: Sandbox,
        checkout: Path,
        worktree: Worktree,
        branch: str,
        *,
        merge_to_head: bool,
        apply_to_host: Callable[[], None] | None,
        cancel: threading.Event,
    ) -> None:
        self._git = git
        self._sandbox = sandbox
        self._checkout = checkout
        self._worktree = worktree
        self._branch = branch
        self._merge_to_head = merge_to_head
        self._apply_to_host = apply_to_host
        self._cancel = cancel
        self._closed = False

    @property
    def worktree(self) -> Worktree:
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
            self._git.commits,
            self._git.branches,
            self._git.worktrees,
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
                self._git.worktrees.remove(self._worktree, force=True)

    def __enter__(self) -> WorktreeSandbox:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # A cancelled run with uncommitted work keeps its worktree for the user.
        self.close(keep_worktree=isinstance(exception, Cancelled) and self._git.worktrees.has_changes(self._worktree))


def create_sandbox(
    git: Git,
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
    target = _default_worktree_path(checkout, harness_root, branch) if branch is not None else None
    prepared = git.branches.prepare(Branch(checkout, branch) if branch else None, Branch(checkout, base), target)
    if target is None:
        target = _default_worktree_path(checkout, harness_root, prepared.name)
    worktree = git.worktrees.create(prepared, target)
    _exclude_workspace(harness_root)
    try:
        run_host_hooks(git.worktrees, hooks.worktree_ready, worktree.path, cancel=cancel)
        sandbox = sandbox_factory(harness_root, cancel)
        try:
            run_host_hooks(git.worktrees, hooks.sandbox_ready, worktree.path, cancel=cancel)
        except Exception:
            sandbox.close()
            raise
    except Cancelled as exception:
        if git.worktrees.has_changes(worktree):
            raise Cancelled(worktree.path) from exception
        git.worktrees.remove(worktree, force=True)
        raise
    except Exception:
        git.worktrees.remove(worktree, force=True)
        raise
    return WorktreeSandbox(
        git,
        sandbox,
        checkout,
        worktree,
        prepared.name,
        merge_to_head=merge_to_head,
        apply_to_host=apply_to_host,
        cancel=cancel,
    )

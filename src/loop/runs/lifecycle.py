from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from loop.contracts.agent_client import AgentBinding, AgentClientFactory, AgentOptions, AgentResult
from loop.errors import Cancelled, LoopError
from loop.platforms.git import Branch, BranchService, CommitService, Git, Hook, Worktree, WorktreeService
from loop.process import CommandExecutor, CommandResult, OnLine, execute


@dataclass(frozen=True)
class LifecycleResult[T]:
    result: T
    branch: str
    commits: tuple[str, ...]


def run_host_hooks(
    worktrees: WorktreeService, hooks: Sequence[Hook], worktree: Path, *, cancel: threading.Event | None = None
) -> None:
    """Run each Hook on the host in the worktree, in order; the first failure stops the rest."""
    for hook in hooks:
        worktrees.run_hook(hook, worktree, cancel)


def run_lifecycle[T](
    commits: CommitService,
    branches: BranchService,
    worktrees: WorktreeService,
    checkout: Path,
    worktree: Worktree,
    work: Callable[[str], T],
    *,
    branch: str | None = None,
    keep_source_branch: bool = False,
) -> LifecycleResult[T]:
    """Wrap one unit of agent work with the base head before and commit collection after.

    With `branch=None` the worktree is on a temp branch that is merged into the host checkout's
    current branch afterwards; otherwise the commits stay on `branch`.
    """
    host = worktrees.get(checkout) if branch is None else None
    host_branch = host.branch.name if host is not None and host.branch is not None else None
    if branch is None and host_branch is None:
        raise LoopError(f"cannot merge into a detached HEAD in {checkout}")
    if worktree.branch is None:
        raise LoopError(f"worktree is on a detached HEAD: {worktree.path}")
    worktree_branch = worktree.branch.name

    base_head = commits.head(worktree.path)
    result = work(base_head.sha)

    new_commits = commits.since(base_head)
    if branch is None:
        branches.merge(checkout, Branch(checkout, worktree_branch))
        if not keep_source_branch:
            worktrees.detach(worktree)
            branches.delete(Branch(checkout, worktree_branch))
    return LifecycleResult(result, worktree_branch, tuple(commit.sha for commit in new_commits))


@dataclass(frozen=True)
class WorktreeRunResult:
    result: AgentResult
    branch: str
    commits: tuple[str, ...]


class WorktreeRunner:
    """A long-lived worktree whose agents run on the host from the harness root; each `run` collects its commits."""

    def __init__(
        self,
        git: Git,
        harness_root: Path,
        checkout: Path,
        worktree: Worktree,
        branch: str,
        *,
        merge_to_head: bool,
        executor: CommandExecutor | None,
        cancel: threading.Event,
    ) -> None:
        self._git = git
        self._harness_root = harness_root
        self._checkout = checkout
        self._worktree = worktree
        self._branch = branch
        self._merge_to_head = merge_to_head
        self._executor = executor or self._host_executor
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
    ) -> WorktreeRunResult:
        outcome = run_lifecycle(
            self._git.commits,
            self._git.branches,
            self._git.worktrees,
            self._checkout,
            self._worktree,
            lambda _base_head: self._run_agent(agent, prompt, prompt_args, options),
            branch=None if self._merge_to_head else self._branch,
            keep_source_branch=self._merge_to_head,
        )
        return WorktreeRunResult(outcome.result, outcome.branch, outcome.commits)

    def close(self, *, keep_worktree: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        if not keep_worktree:
            self._git.worktrees.remove(self._worktree, force=True)

    def __enter__(self) -> WorktreeRunner:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # A cancelled run with uncommitted work keeps its worktree for the user.
        self.close(keep_worktree=isinstance(exception, Cancelled) and self._git.worktrees.has_changes(self._worktree))

    def _run_agent(
        self,
        agent: AgentClientFactory,
        prompt: str,
        prompt_args: Mapping[str, str] | None,
        options: AgentOptions | None,
    ) -> AgentResult:
        binding = AgentBinding(self._bound_executor, str(self._harness_root))
        result = agent(binding).run(prompt, prompt_args, options)
        if self._cancel.is_set():
            raise Cancelled()
        return result

    def _bound_executor(
        self, command: Sequence[str] | str, *, timeout_s: float | None = None, on_line: OnLine | None = None
    ) -> CommandResult:
        return self._executor(command, timeout_s=timeout_s, on_line=on_line, cancel=self._cancel)

    def _host_executor(
        self,
        command: Sequence[str] | str,
        *,
        timeout_s: float | None = None,
        on_line: OnLine | None = None,
        cancel: threading.Event | None = None,
    ) -> CommandResult:
        return execute(command, cwd=self._harness_root, timeout_s=timeout_s, on_line=on_line, cancel=cancel)


def create_worktree_runner(
    git: Git,
    *,
    checkout: Path,
    harness_root: Path,
    worktree_root: Path,
    base: str,
    branch: str | None = None,
    hooks: Sequence[Hook] = (),
    merge_to_head: bool = False,
    executor: CommandExecutor | None = None,
    cancel: threading.Event | None = None,
) -> WorktreeRunner:
    """Create the worktree and run the `worktree-ready` hooks.

    `merge_to_head` merges each run's commits into the checkout's current branch and keeps the
    worktree on its branch. A branch named `loop/run-<id>` is generated when none is given.
    `executor` defaults to running commands on the host in the harness root.
    """
    cancel = cancel or threading.Event()
    target = worktree_root / branch if branch is not None else None
    prepared = git.branches.prepare(Branch(checkout, branch) if branch else None, Branch(checkout, base), target)
    if target is None:
        target = worktree_root / prepared.name
    worktree = git.worktrees.create(prepared, target)
    try:
        run_host_hooks(git.worktrees, hooks, worktree.path, cancel=cancel)
    except Cancelled as exception:
        if git.worktrees.has_changes(worktree):
            raise Cancelled(worktree.path) from exception
        git.worktrees.remove(worktree, force=True)
        raise
    except Exception:
        git.worktrees.remove(worktree, force=True)
        raise
    return WorktreeRunner(
        git,
        harness_root,
        checkout,
        worktree,
        prepared.name,
        merge_to_head=merge_to_head,
        executor=executor,
        cancel=cancel,
    )

from __future__ import annotations

import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import TracebackType

from loop.contracts.agent_client import AgentBinding, AgentClient, AgentClientFactory, AgentOptions, AgentResult
from loop.errors import Cancelled, LoopError
from loop.platforms.git import Branch, Commit, Git, Hook, RepositoryData, Worktree, WorktreeService
from loop.process import CommandExecutor, CommandResult, OnLine, execute
from loop.prompt import Prompt


def run_host_hooks(
    worktrees: WorktreeService, hooks: Sequence[Hook], worktree: Path, *, cancel: threading.Event | None = None
) -> None:
    """Run each Hook on the host in the worktree, in order; the first failure stops the rest."""
    for hook in hooks:
        worktrees.run_hook(hook, worktree, cancel)


@dataclass(frozen=True)
class AgentRunResult:
    result: AgentResult
    branch: str
    commits: tuple[str, ...]
    session_key: str | None

class WorktreeLifecycle:
    """Owns one worktree from creation to disposal and collects the commits each run adds to it."""

    def __init__(self, git: Git, worktree: Worktree, branch: str) -> None:
        self._git = git
        self._worktree = worktree
        self._branch = branch
        self._closed = False

    @property
    def worktree(self) -> Worktree:
        return self._worktree

    @property
    def branch(self) -> str:
        return self._branch

    def begin_run(self) -> Commit:
        """Validates the branch a run needs and returns the head the run starts from."""
        if self._worktree.branch is None:
            raise LoopError(f"worktree is on a detached HEAD: {self._worktree.path}")
        return self._git.commits.head(self._worktree.path)

    def end_run(self, base_head: Commit) -> tuple[str, ...]:
        """Returns the commits added since `base_head`."""
        new_commits = self._git.commits.since(base_head)
        return tuple(commit.sha for commit in new_commits)

    def has_changes(self) -> bool:
        return self._git.worktrees.has_changes(self._worktree)

    def exit(self, *, keep_worktree: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        if not keep_worktree:
            self._git.worktrees.remove(self._worktree, force=True)


class AgentRunner:
    """One agent client on one worktree; leaving a `with` block disposes the client and then the worktree."""

    def __init__(self, lifecycle: WorktreeLifecycle, client: AgentClient, cancel: threading.Event) -> None:
        self.lifecycle = lifecycle
        self._client = client
        self._cancel = cancel

    @property
    def worktree(self) -> Worktree:
        return self.lifecycle.worktree

    @property
    def branch(self) -> str:
        return self.lifecycle.branch

    def run(
        self,
        prompt: Prompt,
        model: str | None = None,
        reasoning_effort: str | None = None,
        options: AgentOptions | None = None,
        *,
        new_session: bool = False,
    ) -> AgentRunResult:
        """Continues the conversation `options.session_key` names, starts a resumable one when `new_session`, else runs stateless.

        The result carries the session key used, to pass back in the next run's options.
        """
        options = options or AgentOptions()
        if options.session_key is None and new_session:
            options = replace(options, session_key=uuid.uuid4().hex)
        base_head = self.lifecycle.begin_run()

        result = self._client.run(prompt, model, reasoning_effort, options)
        if self._cancel.is_set():
            raise Cancelled()

        return AgentRunResult(result, self.branch, self.lifecycle.end_run(base_head), options.session_key)

    def exit(self, *, keep_worktree: bool = False) -> None:
        try:
            self._client.exit()
        finally:
            self.lifecycle.exit(keep_worktree=keep_worktree)

    def __enter__(self) -> AgentRunner:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # A cancelled run with uncommitted work keeps its worktree for the user.
        self.exit(keep_worktree=isinstance(exception, Cancelled) and self.lifecycle.has_changes())


class AgentRunnerProvider:
    """Creates runners whose agent runs on the host from the harness root."""

    def __init__(
        self,
        git: Git,
        harness_root: Path,
        agent: AgentClientFactory,
        *,
        executor: CommandExecutor | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        self._git = git
        self._harness_root = harness_root
        self._agent = agent
        self._executor = executor or self._host_executor
        self._cancel = cancel or threading.Event()

    def create(
        self,
        repository: RepositoryData,
        *,
        base: str,
        branch: str | None = None,
        hooks: Sequence[Hook] = (),
    ) -> AgentRunner:
        """Create the worktree, run the `worktree-ready` hooks, and bind the agent client to it.

        A branch named `loop/run-<id>` is generated when none is given.
        """
        worktree, branch_name = self._create_worktree(repository, base, branch, hooks)
        lifecycle = WorktreeLifecycle(self._git, worktree, branch_name)
        try:
            client = self._agent(AgentBinding(self._bound_executor, str(self._harness_root)))
        except Exception:
            lifecycle.exit()
            raise
        return AgentRunner(lifecycle, client, self._cancel)

    def _create_worktree(
        self, repository: RepositoryData, base: str, branch: str | None, hooks: Sequence[Hook]
    ) -> tuple[Worktree, str]:
        checkout, worktree_root = repository.path, repository.worktree_root
        target = worktree_root / branch if branch is not None else None
        prepared = self._git.branches.prepare(
            Branch(checkout, branch) if branch else None, Branch(checkout, base), target
        )
        if target is None:
            target = worktree_root / prepared.name
        worktree = self._git.worktrees.create(prepared, target)
        try:
            run_host_hooks(self._git.worktrees, hooks, worktree.path, cancel=self._cancel)
        except Cancelled as exception:
            if self._git.worktrees.has_changes(worktree):
                raise Cancelled(worktree.path) from exception
            self._git.worktrees.remove(worktree, force=True)
            raise
        except Exception:
            self._git.worktrees.remove(worktree, force=True)
            raise
        return worktree, prepared.name

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

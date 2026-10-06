from __future__ import annotations

import shlex
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from orb.contracts.capsule import Capsule
from orb.errors import CommandError, OrbError
from orb.platforms.git_client import GitClient, Hook
from orb.process import TRANSIENT_EXIT_CODES, TRANSIENT_RETRIES, TRANSIENT_RETRY_DELAY_S


@dataclass(frozen=True)
class SandboxHooks:
    """Host-side shell commands: `worktree_ready` once the worktree exists, `sandbox_ready` once the Capsule is up."""

    worktree_ready: tuple[Hook, ...] = ()
    sandbox_ready: tuple[Hook, ...] = ()


@dataclass(frozen=True)
class LifecycleResult[T]:
    result: T
    branch: str
    commits: tuple[str, ...]


def run_host_hooks(
    git: GitClient, hooks: Sequence[Hook], worktree: Path, *, cancel: threading.Event | None = None
) -> None:
    """Run each Hook on the host in the worktree, in order; the first failure stops the rest."""
    for hook in hooks:
        git.run_hook(hook, worktree, cancel)


def with_sandbox_lifecycle[T](
    git: GitClient,
    capsule: Capsule,
    checkout: Path,
    worktree: Path,
    work: Callable[[str], T],
    *,
    branch: str | None = None,
    apply_to_host: Callable[[], None] | None = None,
    on_sandbox_ready: Sequence[Hook] = (),
    keep_source_branch: bool = False,
    cancel: threading.Event | None = None,
    sleep: Callable[[float], None] | None = None,
) -> LifecycleResult[T]:
    """Wrap one unit of agent work with setup before and commit collection after.

    With `branch=None` the worktree is on a temp branch that is merged into the host checkout's
    current branch afterwards; otherwise the commits stay on `branch`.
    """
    host_branch = git.current_branch(checkout) if branch is None else None
    if branch is None and host_branch is None:
        raise OrbError(f"cannot merge into a detached HEAD in {checkout}")
    if capsule.isolated:
        identity = {key: git.config_get(checkout, key) for key in ("user.name", "user.email")}
        _prepare_capsule(capsule, worktree, identity, sleep or time.sleep)
    worktree_branch = git.current_branch(worktree)
    if worktree_branch is None:
        raise OrbError(f"worktree is on a detached HEAD: {worktree}")
    run_host_hooks(git, on_sandbox_ready, worktree, cancel=cancel)

    base_head = git.head(worktree)
    result = work(base_head)
    if apply_to_host is not None:
        apply_to_host()

    commits = git.commits_between(worktree, base_head, git.head(worktree))
    if branch is None:
        git.merge(checkout, worktree_branch)
        if not keep_source_branch:
            git.detach(worktree)
            git.delete_branch(checkout, worktree_branch)
    return LifecycleResult(result, worktree_branch, tuple(commits))


def _prepare_capsule(
    capsule: Capsule, worktree: Path, identity: dict[str, str | None], sleep: Callable[[float], None]
) -> None:
    """Trust the worktree and commit as the host user inside an isolated Capsule."""
    path = shlex.quote(str(worktree))
    commands = [
        f"git config --global --get-all safe.directory | grep -qxF {path}"
        f" || git config --global --add safe.directory {path}"
    ]
    commands += [f"git config --global {key} {shlex.quote(value)}" for key, value in identity.items() if value]
    for command in commands:
        _exec_with_retry(capsule, command, sleep)


def _exec_with_retry(capsule: Capsule, command: str, sleep: Callable[[float], None]) -> None:
    attempt = 0
    while True:
        try:
            capsule.exec(command)
            return
        except CommandError as exception:
            if exception.returncode not in TRANSIENT_EXIT_CODES or attempt >= TRANSIENT_RETRIES:
                raise
            attempt += 1
            sleep(TRANSIENT_RETRY_DELAY_S)

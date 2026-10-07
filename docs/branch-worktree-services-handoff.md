# Branch and Worktree Service Contracts

Goal: extract branch and worktree behavior from `GitClient` into focused deep modules.

## Branch entity

`Branch` owns both repository path and branch identity.

```python
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Branch:
    path: Path
    name: str

    @property
    def ref(self) -> str:
        return f"refs/heads/{self.name}"

    @property
    def remote_ref(self) -> str:
        return f"refs/remotes/origin/{self.name}"

    @property
    def upstream(self) -> str:
        return f"origin/{self.name}"
```

`path` is the Git checkout/worktree where commands execute.

## Branch service

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BranchPresence:
    local: bool
    remote: bool


class BranchService(Protocol):
    def presence(self, branch: Branch) -> BranchPresence:
        """
        Determine whether the branch exists locally and on origin.

        Local:
            git show-ref --verify --quiet refs/heads/<branch>

        Remote:
            git show-ref --verify --quiet refs/remotes/origin/<branch>
        """

    def reset(self, branch: Branch, start_ref: str) -> None:
        """
        Force an existing local branch to start_ref.

            git branch -f <branch> <start_ref>
        """

    def delete(self, branch: Branch) -> None:
        """
        Delete the local branch.

            git branch -D <branch>
        """

    def push(self, branch: Branch) -> None:
        """
        Push the branch to origin.

            git push origin <branch>
        """

    def ahead_of_remote(self, branch: Branch, base: str) -> bool:
        """
        Check whether the local branch contains unpublished commits.

        If remote branch exists:
            git rev-list origin/<branch>..<branch>

        Otherwise compare against base:
            git rev-list <base>..<branch>
        """

    def merge(self, target: Path, branch: Branch) -> None:
        """
        Merge branch into another checkout.

            git merge --no-edit <branch>
        """
```

## Worktree entity

```python
@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: Branch | None
```

`branch=None` represents detached HEAD.

## Worktree service

```python
class WorktreeService(Protocol):
    def list(self, checkout: Path) -> list[Worktree]:
        """
        Discover all worktrees.

            git worktree list --porcelain

        Parse:
            worktree <path>
            branch refs/heads/<branch>
        """

    def create(
        self,
        branch: Branch,
        target: Path,
        start_ref: str | None = None,
    ) -> Worktree:
        """
        Create a worktree.

        Existing local branch:
            git worktree add <target> <branch>

        New local branch:
            git worktree add -b <branch> <target> <start_ref>

        start_ref is required only when creating a new branch.
        """

    def remove(
        self,
        checkout: Path,
        worktree: Worktree,
        *,
        force: bool = False,
    ) -> None:
        """
        Remove a worktree.

        Normal:
            git worktree remove <path>

        Forced:
            git worktree remove --force <path>
        """

    def detach(self, worktree: Worktree) -> None:
        """
        Detach HEAD inside the worktree.

            git checkout --detach
        """
```

## Orchestration boundary

Branch/worktree coordination must remain above both services.

Example flow:

```python
branch = Branch(checkout, branch_name)
base_branch = Branch(checkout, base)

presence = branches.presence(branch)

start_ref = (
    branch.upstream
    if presence.remote
    else base_branch.upstream
)

existing = next(
    (
        wt
        for wt in worktrees.list(checkout)
        if wt.branch == branch
    ),
    None,
)

if existing is not None:
    # Validate/remove stale worktree before recreating.
    ...

if presence.local:
    branches.reset(branch, start_ref)

worktree = worktrees.create(
    branch=branch,
    target=target,
    start_ref=None if presence.local else start_ref,
)
```

Responsibilities:

```text
Branch
    path
    name
    local ref
    remote ref
    upstream ref

BranchService
    branch presence
    reset
    delete
    push
    merge
    unpublished/ahead detection

WorktreeService
    create
    list
    remove
    detach

Orchestrator
    choose start_ref
    resolve local vs remote branch state
    detect conflicting/stale worktrees
    coordinate BranchService + WorktreeService
```

Do not put workflow decisions such as “remote branch wins over base” inside `WorktreeService`. It should remain a Git worktree abstraction, not a branch lifecycle orchestrator.

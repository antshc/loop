# Branch and Worktree Service Contracts

Goal: extract branch and worktree behavior from `GitClient` into focused deep modules.

The caller should express intent. Local/remote ref checks and Git command selection stay inside the services.

## Branch entity

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

`path` is the Git checkout/worktree used to execute branch commands.

## Branch service

```python
from typing import Protocol


class BranchService(Protocol):
    def prepare(self, branch: Branch, base: Branch) -> Branch:
        """
        Ensure branch exists locally at the correct starting ref.

        Internal logic:

        1. Validate branch name:
             git check-ref-format --branch <branch>

        2. Check refs internally:
             git show-ref --verify --quiet refs/remotes/origin/<branch>
             git show-ref --verify --quiet refs/heads/<branch>

        3. Select start ref:
             remote branch exists -> origin/<branch>
             otherwise            -> origin/<base>

        4. Existing local branch:
             git branch -f <branch> <start-ref>

           Missing local branch:
             git branch <branch> <start-ref>

        Caller does not inspect local/remote presence.
        """

    def delete(self, branch: Branch) -> None:
        """
        Delete local branch.

            git branch -D <branch>
        """

    def push(self, branch: Branch) -> None:
        """
        Push branch to origin.

            git push origin <branch>
        """

    def ahead_of_remote(self, branch: Branch, base: Branch) -> bool:
        """
        Return whether local branch has unpublished commits.

        Internal logic:

        - if origin/<branch> exists:
              git rev-list origin/<branch>..<branch>
        - otherwise:
              git rev-list origin/<base>..<branch>
        """

    def merge(self, target: Path, branch: Branch) -> None:
        """
        Merge branch into target checkout.

            git merge --no-edit <branch>
        """
```

There is intentionally no public `presence()`, `exists_local()`, or
`exists_remote()`. Those are implementation details of branch operations.

## Worktree entity

```python
@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: Branch | None
```

`branch=None` means detached HEAD.

When parsed from Git, the branch entity uses the worktree path:

```python
Worktree(
    path=worktree_path,
    branch=Branch(path=worktree_path, name=branch_name),
)
```

Branch conflicts are compared by branch name, not full `Branch` equality,
because the same repository branch can be represented from different checkout paths.

## Worktree service

```python
class WorktreeService(Protocol):
    def create(self, branch: Branch, target: Path) -> Worktree:
        """
        Attach a prepared local branch to target.

        Internal logic:

        1. Discover registered worktrees:
             git worktree list --porcelain

        2. If <branch> is checked out at another path:
             fail

        3. If target is already a registered worktree:
             - dirty target -> fail
             - clean target -> remove stale worktree:
                   git worktree remove --force <target>

        4. Create:
             git worktree add <target> <branch>

        Branch creation/start-ref selection does not belong here.
        """

    def list(self, checkout: Path) -> list[Worktree]:
        """
        Discover worktrees.

            git worktree list --porcelain

        Parse worktree path and optional refs/heads/<branch>.
        """

    def remove(self, worktree: Worktree, *, force: bool = False) -> None:
        """
        Remove worktree using its repository context.

        Normal:
            git worktree remove <path>

        Forced:
            git worktree remove --force <path>
        """

    def detach(self, worktree: Worktree) -> Worktree:
        """
        Detach HEAD.

            git checkout --detach

        Return the detached representation:
            Worktree(path=worktree.path, branch=None)
        """
```

## Intended orchestration

```python
branch = branches.prepare(
    Branch(path=checkout, name=branch_name),
    base=Branch(path=checkout, name=base_name),
)

worktree = worktrees.create(
    branch=branch,
    target=target,
)
```

The orchestrator no longer knows:

- whether the branch exists locally;
- whether the branch exists remotely;
- which ref should be used as the start ref;
- whether `git branch -f` or `git branch` is required;
- whether `git worktree add -b` is required.

`BranchService.prepare()` ensures the local branch exists first, therefore
`WorktreeService.create()` always uses:

```text
git worktree add <target> <branch>
```

## Responsibility boundary

```text
Branch
    path
    name
    local ref
    remote ref
    upstream ref

BranchService
    hide local/remote presence
    choose branch start ref
    prepare/reset/create local branch
    delete
    push
    merge
    unpublished/ahead detection

WorktreeService
    hide worktree registry parsing
    detect branch checkout conflicts
    reconcile stale target worktrees
    create
    list
    remove
    detach

Orchestrator
    choose desired branch
    choose base branch
    choose target path
    coordinate BranchService -> WorktreeService
```

Deep-module rule: do not leak Git state-selection mechanics to the workflow.
The workflow says which branch/base/target it wants; the services decide how Git
must achieve that state.

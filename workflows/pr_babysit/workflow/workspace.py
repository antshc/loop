from __future__ import annotations

from contextlib import contextmanager

from loop import BranchStrategy, GitOptions

from ..infrastructure.github import digest
from ..infrastructure.state import Blocked, process_lock


@contextmanager
def open_workspace(settings, repository, snapshot, state, git, observer, new_agent):
    """Serialize a named branch and compose Loop's built-in recoverable worktree lifecycle."""
    branch = state.data.setdefault("worktree_branch", f"babysit/{repository.name}/{snapshot['pr']['number']}")
    root = settings.state_dir / "worktrees"
    lock = settings.state_dir / "locks" / f"{digest([str(repository.path), branch])}.lock"
    with process_lock(lock):
        batch = state.data.get("batch")
        # A missing worktree is normal after a clean failure; its branch retains the commits.
        start = batch["head"] if batch else snapshot["pr"]["head"]["sha"]
        git.fetch_revision(repository.path, snapshot["pr"]["head"]["sha"])
        builder = new_agent()
        if observer is not None:
            builder.with_observer(observer)
        options = GitOptions(root_path=root, repository_path=repository.path, strategy=BranchStrategy(branch, start))
        with builder.with_git(options).open() as workspace:
            if not batch:
                if not git.clean(workspace.path):
                    raise Blocked("Unverified edits retained in worktree")
                actual = git.head(workspace.path)
                expected = state.data.get("worktree_base", start)
                if actual != start:
                    if actual != expected:
                        raise Blocked("Unexpected local commits; inspect recovery branch")
                    git.advance(workspace.path, start)
                state.data["worktree_base"] = start
            state.save()
            yield workspace

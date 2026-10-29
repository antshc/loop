"""The recovery worktree one PR's agents edit, serialized per branch."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from loop import AgentWorkspace, BranchStrategy, GitOptions

from software_factory.shared.agents.launcher import AgentLauncher
from software_factory.shared.git import GitClient

from ..model import Blocked, PrState, exclusive
from ..settings import Repository, Settings
from ..watch.feedback import digest


class PrWorkspace:
    """Opens Loop's recoverable worktree on the PR's named recovery branch."""

    def __init__(self, settings: Settings, repository: Repository, state: PrState,
                 git: GitClient, launch: AgentLauncher) -> None:
        self._settings = settings
        self._repository = repository
        self._state = state
        self._git = git
        self._launch = launch

    @contextmanager
    def open(self, snapshot: dict) -> Iterator[AgentWorkspace]:
        state, git = self._state, self._git
        branch = state.data.setdefault("worktree_branch", f"babysit/{self._repository.name}/{snapshot['pr']['number']}")
        root = self._settings.state_dir / "worktrees"
        lock = self._settings.state_dir / "locks" / f"{digest([str(self._repository.path), branch])}.lock"
        with exclusive(lock):
            batch = state.data.get("batch")
            # A missing worktree is normal after a clean failure; its branch retains the commits.
            start = batch["head"] if batch else snapshot["pr"]["head"]["sha"]
            git.fetch_revision(self._repository.path, snapshot["pr"]["head"]["sha"])
            options = GitOptions(root_path=root, repository_path=self._repository.path,
                                 strategy=BranchStrategy(branch, start))
            with self._launch().with_git(options).open() as workspace:
                if not batch:
                    if not git.is_clean(workspace.path):
                        raise Blocked("Unverified edits retained in worktree")
                    actual = git.head(workspace.path).sha
                    expected = state.data.get("worktree_base", start)
                    if actual != start:
                        if actual != expected:
                            raise Blocked("Unexpected local commits; inspect recovery branch")
                        git.advance(workspace.path, start)
                    state.data["worktree_base"] = start
                state.save()
                yield workspace

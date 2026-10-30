"""The workspace openers (the harness checkout, the feature worktree with its Loop hooks and a pull request's recovery worktree) and the clean-tree check, over a real temporary repository."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from loop import (
    AgentBuilder, AgentContext, AgentWorkspace, DockerRuntime, FakeCliRunner, FakeReply, GitCli, GitRuntime, WorktreeError,
    WorktreeReadyLoopHook,
)

from software_factory.runtime import Evidence, Flow, Hold, Nothing, Pass, RunResult, Stage, Workflow
from software_factory.shared.agents.launcher import AgentLauncher
from software_factory.stages import specs, workspaces
from software_factory.tests.runtime.harness import command
from software_factory.tests.stages.pr_support import AddressAgent, PrRig, PrWork, workspace_at
from software_factory.tests.stages.support import BRANCH, StageRig


@dataclass(frozen=True)
class InWorkspace:
    workspace: AgentWorkspace


def launcher(path: Path) -> AgentLauncher:
    """A launcher whose agents start in `path`, run real Git and answer with an empty reply."""
    return AgentLauncher(
        lambda: AgentBuilder(FakeCliRunner(lambda *_: FakeReply("")), GitRuntime(GitCli()), DockerRuntime(), AgentContext(cwd=path)),
    )


def record_workspace(paths: list[Path], branches: list[str]) -> Stage[Any, Any, Any]:
    """A Stage that notes the path and branch of the workspace it runs in."""
    def note(i: InWorkspace) -> None:
        paths.append(i.workspace.path)
        branches.append(command(("branch", "--show-current"), i.workspace.path).strip())

    return Stage("note-workspace", InWorkspace, produces=Nothing).action(note).gate(lambda *_: Pass(Nothing()))


def test_the_checkout_opener_runs_stages_in_the_harness_checkout(tmp_path: Path) -> None:
    """Given a launcher that starts in the harness checkout, when a Stage runs in the checkout scope, then its workspace is that checkout."""
    rig = StageRig(tmp_path)
    paths: list[Path] = []
    workflow = rig.workflow(specs.prepare_checkout()).within(workspaces.checkout(), Flow("c").stage(record_workspace(paths, [])))

    result = rig.run(workflow, launcher(rig.repo.path))

    assert result.status == "completed"
    assert paths == [rig.repo.path]


def test_the_feature_worktree_opener_creates_the_feature_branch_from_origins_base_and_runs_the_hooks(tmp_path: Path) -> None:
    """Given a routed Spec and a ready hook, when a Stage runs in the feature worktree, then it is on the feature branch under the target's worktrees and the hook ran."""
    rig = StageRig(tmp_path)
    paths: list[Path] = []
    branches: list[str] = []
    hook = WorktreeReadyLoopHook("touch ../hook-ran")
    workflow = rig.workflow(specs.prepare_checkout()).within(
        workspaces.feature_worktree(hooks=(hook,)), Flow("w").stage(record_workspace(paths, branches)),
    )

    result = rig.run(workflow, launcher(rig.repo.path))

    assert result.status == "completed"
    assert paths == [rig.target.worktrees / BRANCH]
    assert branches == [BRANCH]
    assert (rig.target.worktrees / "hook-ran").exists()
    assert command(("rev-parse", BRANCH), rig.repo.path) == command(("rev-parse", "origin/main"), rig.repo.path)


def pr_workflow(rig: PrRig, paths: list[Path], branches: list[str]) -> Workflow[PrWork]:
    """Poll the pull request, then note the workspace of its recovery worktree scope."""
    return (
        Workflow("pr-workspace").context(PrWork)
        .stage(rig.poll())
        .within(workspaces.pr_worktree(), Flow("w").stage(record_workspace(paths, branches)))
    )


def open_pr_worktree(rig: PrRig) -> tuple[RunResult, list[Path], list[str]]:
    paths: list[Path] = []
    branches: list[str] = []
    result = rig.run(pr_workflow(rig, paths, branches), AddressAgent({}))
    return result, paths, branches


def test_the_pr_worktree_opener_starts_the_recovery_branch_at_the_pull_request_head(tmp_path: Path) -> None:
    """Given a pull request, when a Stage runs in its worktree scope, then the worktree is on `babysit/<repository>/<number>` at the pull request head."""
    rig = PrRig(tmp_path)

    result, paths, branches = open_pr_worktree(rig)

    assert result.status == "completed"
    assert paths == [rig.worktree()] and branches == [rig.recovery_branch()]
    assert command(("rev-parse", rig.recovery_branch()), rig.repo.path).strip() == rig.head


def test_the_pr_worktree_opener_keeps_the_commits_an_earlier_run_left_ahead_of_the_head(tmp_path: Path) -> None:
    """Given a recovery branch with a commit beyond the pull request head, when the scope opens, then the commit stays."""
    rig = PrRig(tmp_path)
    command(("branch", rig.recovery_branch(), rig.head), rig.repo.path)
    command(("worktree", "add", "-q", str(rig.worktree()), rig.recovery_branch()), rig.repo.path)
    (rig.worktree() / "kept.txt").write_text("kept\n")
    command(("add", "-A"), rig.worktree())
    command(("commit", "-q", "-m", "kept"), rig.worktree())
    kept = command(("rev-parse", "HEAD"), rig.worktree()).strip()

    result, _, _ = open_pr_worktree(rig)

    assert result.status == "completed"
    assert command(("rev-parse", rig.recovery_branch()), rig.repo.path).strip() == kept


def test_the_pr_worktree_opener_advances_a_recovery_branch_that_is_behind_the_head(tmp_path: Path) -> None:
    """Given a recovery branch behind the pull request head, when the scope opens, then it is fast-forwarded to the head."""
    rig = PrRig(tmp_path)
    command(("branch", rig.recovery_branch(), "main"), rig.repo.path)

    result, _, _ = open_pr_worktree(rig)

    assert result.status == "completed"
    assert command(("rev-parse", rig.recovery_branch()), rig.repo.path).strip() == rig.head


def test_the_pr_worktree_opener_refuses_a_recovery_branch_that_diverged_from_the_head(tmp_path: Path) -> None:
    """Given a recovery branch with a commit that is not on the pull request head, when the scope opens, then it raises WorktreeError and no Stage runs."""
    rig = PrRig(tmp_path)
    rig.repo.write("other.txt", "other\n")
    command(("checkout", "-q", "-b", rig.recovery_branch(), "main"), rig.repo.path)
    rig.repo.commit("elsewhere")
    command(("checkout", "-q", "main"), rig.repo.path)
    paths: list[Path] = []

    with pytest.raises(WorktreeError, match="Unexpected local commits"):
        rig.run(pr_workflow(rig, paths, []), AddressAgent({}))

    assert paths == []


def test_require_clean_passes_on_a_clean_worktree_and_holds_on_any_change(tmp_path: Path) -> None:
    """Given a clean worktree and one with an untracked file, when require-clean runs, then it passes for the first and holds for the second."""
    rig = PrRig(tmp_path)
    stage = workspaces.require_clean()

    def verdict_in(path: Path):
        work = workspaces.CleanInput(workspace_at(path), rig.git)
        return stage.judge(work, stage.executor.run(work), Evidence("", "", False))

    clean = verdict_in(rig.repo.path)
    (rig.repo.path / "junk.txt").write_text("x\n")
    dirty = verdict_in(rig.repo.path)

    assert clean == Pass(Nothing()) and dirty == Hold("Worktree contains unrelated edits")

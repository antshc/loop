"""The workspace openers: the harness checkout and the feature worktree with its Loop hooks, over a real temporary repository."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loop import (
    AgentBuilder, AgentContext, AgentWorkspace, DockerRuntime, FakeCliRunner, FakeReply, GitCli, GitRuntime, WorktreeReadyLoopHook,
)

from software_factory.runtime import Flow, Nothing, Pass, Stage
from software_factory.shared.agents.launcher import AgentLauncher
from software_factory.stages import specs, workspaces
from software_factory.tests.runtime.harness import command
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

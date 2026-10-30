"""Repository contracts: the worktree an attempt runs in, the evidence it leaves, and the violations a contract rejects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..event import Event
from ..stage import AgentCommits, Evidence, HarnessCommits, NoRepository, ReadOnly, Retain, WorkContract
from ..worktree import WorktreeGit


@dataclass(frozen=True)
class Worktree:
    """The Git service bound to the path of the workspace an attempt runs in."""

    git: WorktreeGit
    path: Path

    def evidence(self, base: str, status_before: str) -> Evidence:
        """What the attempt left behind: the head now and whether the head or the working tree moved."""
        head = self.git.head(self.path)
        return Evidence(base, head, changed=head != base or self.git.status(self.path) != status_before)


def violation[I](work: WorkContract, stage_input: I, event: Event, evidence: Evidence, worktree: Worktree) -> str | None:
    """Why an accepted attempt breaks its repository contract; None when it keeps it."""
    match work:
        case ReadOnly():
            return _read_only_violation(evidence)
        case AgentCommits():
            return _agent_commit_violation(work, stage_input, event, evidence, worktree)
        case HarnessCommits() if evidence.head != evidence.base:
            return "the agent moved the branch head; only the runtime commits"
    return None


def rolls_back(work: WorkContract) -> bool:
    """Whether a failed attempt returns the branch to where it started; only a retained attempt keeps its working tree."""
    match work:
        case NoRepository():
            return False
        case HarnessCommits(on_fail=Retain()):
            return False
    return True


def _read_only_violation(evidence: Evidence) -> str | None:
    if evidence.head != evidence.base:
        return "a read-only attempt moved the branch head"
    if evidence.changed:
        return "a read-only attempt changed the working tree"
    return None


def _agent_commit_violation[I](
    work: AgentCommits[I, Event], stage_input: I, event: Event, evidence: Evidence, worktree: Worktree,
) -> str | None:
    commits = worktree.git.commits_since(worktree.path, evidence.base)
    if len(commits) != 1:
        return f"expected exactly one new commit, found {len(commits)}"
    prefix = work.subject(stage_input)
    if not commits[0].subject.startswith(prefix):
        return f"commit subject {commits[0].subject!r} lacks the required prefix {prefix!r}"
    if not worktree.git.is_clean(worktree.path):
        return "the working tree is not clean after the commit"
    reported = work.reported(event)
    if reported != evidence.head:
        return f"reported commit {reported} is not the branch head {evidence.head}"
    return None

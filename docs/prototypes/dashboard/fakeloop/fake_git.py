"""A fake commit: a `code` phase that logs what a real commit would contain."""

from __future__ import annotations

from dataclasses import dataclass

from .model import PhaseParams, new_id
from .runner import Run


@dataclass(frozen=True)
class FakeCommit:
    """A commit that exists only as a trace event."""

    subject: str
    files: tuple[str, ...]
    insertions: int
    deletions: int


def fake_commit(run: Run, name: str, branch: str, commit: FakeCommit) -> str:
    """Runs a `git` code phase that logs the commit and returns its fake SHA."""
    sha = new_id(40)
    params = PhaseParams(name=name, kind="code", owner="git",
                         description=f"Commit the agent's working tree to {branch}.")
    with run.phase(params) as ph:
        run.sleep(0.5)
        ph.log(sha=sha[:10], branch=branch, subject=commit.subject,
               files=", ".join(commit.files),
               stat=f"+{commit.insertions} -{commit.deletions}")
    return sha

"""Conflict recovery merges the base tip on origin and rejects only leftover conflict markers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from loop.shared.git import GitClient
from loop.shared.process import CommandError, CommandRunner
from loop.software_factory.events.targets import RepositoryTarget

from software_factory.src.pull_requests.events.pull_requests import (
    MergeConflict,
    PullRequestPolled,
)
from software_factory.src.pull_requests.stages.conflicts import (
    PlanInput,
    _plan,
    _staged_tree,
)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(path: Path, name: str) -> str:
    (path / name).write_text(name)
    _git(path, "add", name)
    _git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)
    return _git(path, "rev-parse", "HEAD")


def test_plan_pins_the_base_tip_on_origin_when_the_payload_base_is_stale(
    tmp_path: Path,
) -> None:
    origin, work = tmp_path / "origin", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    stale = _commit(work, "base")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(work, "checkout", "-q", "-b", "feat")
    head = _commit(work, "feat")
    _git(work, "push", "-q", "origin", "feat")
    _git(work, "checkout", "-q", "--detach", stale)
    tip = _commit(work, "later")
    _git(work, "push", "-q", "origin", "HEAD:refs/heads/main")
    _git(work, "checkout", "-q", "feat")

    target = RepositoryTarget("o/r", work, tmp_path / "worktrees")
    pr = {"head": {"ref": "feat", "sha": head}, "base": {"ref": "main", "sha": stale}}
    pull = SimpleNamespace(
        is_open=True,
        head_ref="feat",
        head_repository="o/r",
        head_sha=head,
        base_ref="main",
        payload=pr,
    )
    github = SimpleNamespace(
        pull_requests=lambda _: SimpleNamespace(pull_request=lambda _: pull)
    )
    planned = _plan(
        PlanInput(
            PullRequestPolled(target, 1, {"pr": pr}),
            MergeConflict(head, "main", stale),
            SimpleNamespace(path=work),  # type: ignore[arg-type]
            GitClient(CommandRunner()),
            github,  # type: ignore[arg-type]
        )
    )

    assert planned.base == tip


def _staged(path: Path, content: str) -> str:
    (path / "f.txt").write_text(content)
    return _staged_tree(
        SimpleNamespace(
            git=GitClient(CommandRunner()), worktree=SimpleNamespace(path=path)
        )
    )  # type: ignore[arg-type]


def test_staged_tree_accepts_whitespace_but_rejects_conflict_markers(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, "seed")

    assert _staged(tmp_path, "trailing   \n\n")

    with pytest.raises(CommandError):
        _staged(tmp_path, "<<<<<<< ours\na\n=======\nb\n>>>>>>> theirs\n")

"""One explicitly selected PR: work identity and blocked-run recovery."""

from __future__ import annotations

from pathlib import Path

from loop import Ledger

from software_factory.src.github import GitHub
from software_factory.src.pull_requests.stages import pull_requests
from software_factory.tests.infrastructure.github.fakes import (
    FakeGhCli,
    FakePullRequestHost,
)
from software_factory.workflows.pr_babysit.definition import NAME
from software_factory.workflows.pr_babysit.intake import release_if_changed, work_of
from software_factory.workflows.pr_babysit.policy import FINGERPRINT, fingerprint
from software_factory.workflows.pr_babysit.settings import Repository, Settings


def test_explicit_pr_has_its_own_work_item(tmp_path: Path) -> None:
    settings = Settings((Repository("a/b", tmp_path, ("build",), ("test",)),), tmp_path)

    first = work_of(settings, "a/b", 1)
    second = work_of(settings, "a/b", 2)

    assert first.id == "a/b#1" and second.id == "a/b#2"
    assert first.target.worktrees == second.target.worktrees


def test_explicit_pr_releases_a_blocked_run_only_after_the_pr_changes(
    tmp_path: Path,
) -> None:
    host = FakePullRequestHost()
    host.add(1, head_sha="first")
    github = GitHub(FakeGhCli(pulls=host))
    settings = Settings((Repository("a/b", tmp_path, ("build",), ("test",)),), tmp_path)
    work = work_of(settings, "a/b", 1)
    ledger = Ledger(settings.ledger_root)
    previous = fingerprint(pull_requests.read(work, github))
    ledger.item(NAME, work.id).seed(
        [
            {"kind": "run-opened", "epoch": 1},
            {
                "kind": "run-closed",
                "status": "handed_off",
                "reason": "needs clarification",
                "attributes": {FINGERPRINT: previous},
            },
        ]
    )

    assert not release_if_changed(ledger, github, work)
    assert not ledger.status(NAME, work.id).released

    host.pulls[1].head_sha = "second"

    assert release_if_changed(ledger, github, work)
    assert ledger.status(NAME, work.id).released

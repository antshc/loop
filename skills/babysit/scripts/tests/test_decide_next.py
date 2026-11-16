"""Babysit only watches draft PRs; ready PRs get conflicts, CI and review threads handled; Jenkins only runs when enabled."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from software_factory.src.pull_requests.events.feedback import DiscussionPending
from software_factory.src.pull_requests.events.pull_requests import (
    CiFailing,
    DraftWaiting,
    MergeConflict,
    PullRequestClosed,
)
from software_factory.src.pull_requests.stages.pull_requests import _decide
from software_factory.workflows.pr_babysit.settings import Settings

HEAD = "a" * 40


def _input(
    *,
    draft: bool = False,
    open_: bool = True,
    failed_ci: bool = True,
    conflict: bool = True,
    discussion: str = "none",
) -> Any:
    pr = {
        "draft": draft,
        "merged": False,
        "mergeable": not conflict,
        "mergeable_state": "dirty" if conflict else "unstable",
        "user": {"login": "me"},
        "head": {"repo": {"full_name": "o/r"}, "sha": HEAD},
        "base": {"ref": "main", "sha": "b" * 40},
        "requested_reviewers": [],
    }
    snapshot = {
        "pr": pr,
        "review": {},
        "checks": [],
        "statuses": [
            {"id": 1, "context": "ci", "state": "failure" if failed_ci else "success"}
        ],
        "runs": [],
    }
    polled = SimpleNamespace(
        is_open=open_,
        head=HEAD,
        snapshot=snapshot,
        login="me",
        target=SimpleNamespace(slug="o/r"),
        discussion={"action": discussion, "threads": []},
    )
    return SimpleNamespace(polled=polled, addressed=(), requested=())


def test_closed_pr_stops() -> None:
    assert isinstance(_decide(_input(open_=False), None, lambda _: False), PullRequestClosed)


def test_ready_pr_repairs_conflicts() -> None:
    assert isinstance(_decide(_input(), None, lambda _: False), MergeConflict)


def test_ready_pr_repairs_failed_ci() -> None:
    decided = _decide(_input(conflict=False), None, lambda _: False)
    assert isinstance(decided, CiFailing)


def test_ready_pr_answers_review_threads() -> None:
    decided = _decide(
        _input(conflict=False, discussion="proceed"), None, lambda _: False
    )
    assert isinstance(decided, DiscussionPending)


@pytest.mark.parametrize("conflict", [False, True])
@pytest.mark.parametrize("action", ["proceed", "stop", "none"])
def test_draft_pr_is_only_watched(conflict: bool, action: str) -> None:
    decided = _decide(
        _input(draft=True, conflict=conflict, discussion=action),
        None,
        lambda _: True,
    )
    assert decided == DraftWaiting(HEAD)


@pytest.mark.parametrize(("jenkins", "loaded"), [(None, False), ({"enabled": False}, False)])
def test_jenkins_skipped_unless_enabled(
    tmp_path: Path, jenkins: dict[str, Any] | None, loaded: bool
) -> None:
    value: dict[str, Any] = {
        "repositories": [
            {"name": "o/r", "path": ".", "build": ["true"], "test": ["true"]}
        ]
    }
    if jenkins is not None:
        value["jenkins"] = jenkins
    path = tmp_path / "c.json"
    path.write_text(json.dumps(value))
    assert (Settings.load(path).jenkins is not None) is loaded

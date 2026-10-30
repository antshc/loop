"""The dry-run seams: in-memory Git over one history, a stateful canned `gh` and the refusal of unsafe combinations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from software_factory.shared.github.issues.client import Issues
from software_factory.shared.github.repo import GitHubRepo
from factory_workflows.implement_spec.dry import DryGh, DryGit, DryHistory, DryWorktreeGit, Fakes

HERE = Path("/work")


def test_an_unknown_seam_and_a_faked_git_without_a_faked_agent_are_refused() -> None:
    """Given a seam that does not exist or a faked git with a real agent, when the fakes are built, then they are refused."""
    with pytest.raises(ValueError, match="unknown seam"):
        Fakes(["network"])
    with pytest.raises(ValueError, match="needs a faked agent"):
        Fakes(["git"])


def test_only_the_faked_seams_are_replaced() -> None:
    """Given only the store faked, when the seams are asked for, then every other one is None and the store is a fresh directory."""
    fakes = Fakes(["store"])

    assert fakes.git() is None and fakes.github() is None and fakes.new_agent() is None and fakes.readers() is None
    ledger = fakes.ledger_root()
    assert ledger is not None and ledger.is_dir()
    assert Fakes([]).ledger_root() is None


def test_the_workflows_git_and_the_runtimes_git_see_one_history() -> None:
    """Given one history, when a commit is made through it, then both Gits see it, can restore it away and know what is pushed."""
    history = DryHistory()
    git, worktree = DryGit(history), DryWorktreeGit(history)
    assert not git.is_ahead(HERE, "feature", "main") and not git.remote_branch_exists(HERE, "feature")

    sha = history.commit("ccode(X|1): one")

    assert git.head(HERE).sha == sha == worktree.head(HERE)
    assert git.is_ahead(HERE, "feature", "main") and git.push_if_ahead(HERE, "feature", "main")
    assert git.remote_branch_exists(HERE, "feature") and not git.push_if_ahead(HERE, "feature", "main")
    assert [commit.sha for commit in worktree.commits_since(HERE, "base0000")] == [sha]
    assert worktree.is_ancestor(HERE, "base0000") and worktree.parents(HERE) == ("base0000",)
    worktree.restore(HERE, "base0000")
    assert git.head(HERE).sha == "base0000" and git.branch_commits(HERE, "main") == []


def test_the_canned_gh_applies_its_writes_to_its_own_state() -> None:
    """Given the canned gh, when a Ticket is closed and labelled, pull requests are made and a Comment is posted, then later reads show them; the first pull request is not listed yet."""
    gh = DryGh()
    issues = Issues(GitHubRepo("antshc", "loop", gh=gh))
    gh(("issue", "close", "10", "--repo", "antshc/loop", "--comment", "done"))
    gh(("issue", "edit", "1", "--repo", "antshc/loop", "--add-label", "hitl"))
    gh(("pr", "create", "--repo", "antshc/loop", "--head", "branch", "--base", "main", "--title", "T", "--body", "B", "--draft"))
    gh(("pr", "create", "--repo", "antshc/loop", "--head", "branch", "--base", "main", "--title", "T", "--body", "B2", "--draft"))
    issues.comment(1, "note", "hello")

    spec = issues.spec(1)

    assert [ticket.number for ticket in spec.tickets] == [11] and spec.awaiting_human
    assert [pull["headRefName"] for pull in json.loads(gh(("pr", "list", "--head", "branch")))] == ["branch"]
    assert json.loads(gh(("pr", "list", "--head", "other"))) == []
    assert any("hello" in comment.body for comment in spec.comments)

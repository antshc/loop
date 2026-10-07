from __future__ import annotations

from pathlib import Path

from loop.platforms.git import Branch, Commit, Worktree


def test_branches_with_the_same_name_are_equal_at_different_paths() -> None:
    assert Branch(Path("/repo"), "feature-x") == Branch(Path("/other/worktree"), "feature-x")


def test_branches_with_different_names_are_not_equal() -> None:
    assert Branch(Path("/repo"), "feature-x") != Branch(Path("/repo"), "feature-y")


def test_commit_message_is_subject_then_a_blank_line_then_the_body() -> None:
    commit = Commit(Path("/repo"), "abc123", "subject", "body")

    assert commit.message == "subject\n\nbody"


def test_commit_message_is_just_the_subject_with_no_body() -> None:
    commit = Commit(Path("/repo"), "abc123", "subject")

    assert commit.message == "subject"


def test_worktree_on_a_detached_head_carries_no_branch() -> None:
    assert Worktree(Path("/repo.worktrees/feature-x")).branch is None

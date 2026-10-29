from __future__ import annotations

from pathlib import Path

import pytest

from software_factory.legacy_slices.implement_spec.model import gates
from software_factory.tests.legacy_slices.implement_spec.doubles import FakeWorkflowGit

PATH = Path("/w")


def test_an_agent_that_moved_head_violates_the_no_commit_gate() -> None:
    git = FakeWorkflowGit()
    head = git.head(PATH).sha

    assert gates.made_no_commit(git, PATH, head) is None
    git.add_commit("agent")
    assert "only the harness commits" in (gates.made_no_commit(git, PATH, head) or "")


def test_a_read_only_agent_must_leave_head_and_the_working_tree_as_they_were() -> None:
    git = FakeWorkflowGit()
    head, before = git.head(PATH).sha, git.status(PATH)

    assert gates.readonly_violation(git, PATH, head, before) is None
    git.edit("src/a.py")
    assert "changed the working tree" in (gates.readonly_violation(git, PATH, head, before) or "")


def test_a_read_only_agent_may_start_on_a_checkout_the_user_already_changed() -> None:
    git = FakeWorkflowGit()
    git.edit("notes.md")
    head, before = git.head(PATH).sha, git.status(PATH)

    assert gates.readonly_violation(git, PATH, head, before) is None


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Add form", "ccode(X|1): Add form"),
        ("ccode(X|1): Add form", "ccode(X|1): Add form"),
        ("Add form\n\nlong body", "ccode(X|1): Add form"),
        ("", "ccode(X|1): fallback"),
        ("x" * 200, "ccode(X|1): " + "x" * 88),
    ],
)
def test_commit_subject_prefixes_the_first_line_of_the_message(message: str, expected: str) -> None:
    assert gates.commit_subject("ccode(X|1): ", message, "fallback") == expected

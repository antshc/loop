from __future__ import annotations

from pathlib import Path

import pytest

from workflows.dev.model import gates
from workflows.tests.dev.doubles import FakeWorkflowGit

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
    ("files", "expected"),
    [
        (["src/WebApp/Page.razor"], []),
        (["proof/run.ts", "src/a.cs"], ["proof/run.ts"]),
        (["e2e/CartTest.spec.ts", ".github/skills/verify-mole/SKILL.md"], ["e2e/CartTest.spec.ts", ".github/skills/verify-mole/SKILL.md"]),
        (["playwright.config.ts"], ["playwright.config.ts"]),
        (["src/proof/notes.md", "tests/e2e/x.cs"], []),
    ],
)
def test_protected_changes_lists_files_inside_the_proof_framework(files: list[str], expected: list[str]) -> None:
    assert gates.protected_changes(files) == expected


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


def _skill(root: Path, folder: str, name: str, frontmatter: str) -> None:
    path = root / folder / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nname: {name}\n{frontmatter}\n---\n# body\n")


def test_verification_skills_are_the_project_skills_that_drive_the_app_to_prove_behavior(tmp_path: Path) -> None:
    _skill(tmp_path, ".github/skills", "verify-mole", "description: Drive and prove the storefront in Chrome.")
    _skill(tmp_path, ".github/skills", "docs", "description: Writes documentation.")
    _skill(
        tmp_path, ".github/skills", "create-verification",
        'description: "Generates a skill that drives your app to prove behavior"\ndisable-model-invocation: true',
    )
    _skill(tmp_path, ".claude/skills", "other", "description: Drives the CLI and shows proof of results.")

    found = gates.find_verification_skills(tmp_path)

    assert [path.parent.name for path in found] == ["verify-mole", "other"]


def test_no_verification_skills_without_a_skills_folder(tmp_path: Path) -> None:
    assert gates.find_verification_skills(tmp_path) == ()

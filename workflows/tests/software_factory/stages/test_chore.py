"""The chore Stage: a best-effort refinement the runtime commits only after verification, run as a one-Stage Workflow."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from loop import Fail

from tests.runtime.harness import Repo, command
from tests.shared.fakes import PR_DIFF
from workflows.software_factory.events.chore import ChoreDiscarded, ChoreRefined
from workflows.software_factory.stages import chore
from workflows.tests.software_factory.stages.support import (
    DeveloperScript,
    StageRig,
    answers,
)

SUBJECT = "refactor(Checkout): tidy the form"


def refines(
    name: str = "form.txt",
    message: str = "tidy the form",
    out_of_scope: tuple[str, ...] = (),
):
    """A chore action that edits a file and proposes `message`."""

    def act(repo: Repo) -> str:
        repo.write(name, "tidier\n")
        return answers(
            "completed",
            commit_message=message,
            summary="Renamed the helper.",
            out_of_scope=list(out_of_scope),
        )(repo)

    return act


def refinement(rig: StageRig, script: DeveloperScript, **options):
    """The routing Stage, then the chore Stage on the feature worktree with a fixed commit subject."""
    subject = options.pop(
        "subject", lambda initiative, proposed: f"refactor({initiative}): {proposed}"
    )
    return rig.in_worktree(script, chore.refine(subject=subject, **options))


def delivered(tmp_path: Path) -> tuple[StageRig, str]:
    """A rig whose feature branch carries one delivered Ticket under a draft pull request."""
    rig = StageRig(tmp_path)
    return rig, rig.deliver()


def test_a_refinement_that_changes_files_is_committed_once_with_the_given_subject(
    tmp_path: Path,
) -> None:
    """Given a chore agent that edits a file, when the chore runs, then exactly one commit with the given subject carries the edit."""
    rig, head = delivered(tmp_path)
    script = DeveloperScript(rig.repo, [refines(out_of_scope=("old bug",))])

    result = rig.run(refinement(rig, script))

    assert result.status == "completed"
    assert rig.repo.subjects()[:2] == [SUBJECT, "ccode(Checkout|10): implement 0"]
    assert (
        command(("rev-list", "--count", f"{head}..HEAD"), rig.repo.path).strip() == "1"
    )
    assert (
        command(("log", "-1", "--format=%b"), rig.repo.path).strip()
        == "Renamed the helper."
    )
    assert rig.history(ChoreRefined) == (
        ChoreRefined(
            "Renamed the helper.", ("old bug",), "tidy the form", committed=True
        ),
    )
    assert rig.repo.status() == ""


def test_the_agent_gets_the_pull_request_its_diff_and_the_branch_commits_and_the_diff_is_removed_afterwards(
    tmp_path: Path,
) -> None:
    """Given a published branch, when the chore runs, then the prompt names the pull request, its captured diff and the commits, and no diff file is left."""
    rig, _ = delivered(tmp_path)
    prompts: list[str] = []

    def inspects(repo: Repo) -> str:
        prompts.append(
            sorted(path.name for path in (repo.path / "bin" / "review_diff").iterdir())
        )
        return answers("completed", commit_message="", summary="nothing to refine")(
            repo
        )

    script = DeveloperScript(rig.repo, [inspects])

    rig.run(refinement(rig, script))

    request = script.requests[0].prompt
    assert (
        "https://github.com/owner/repo/pull/99" in request
        and "ccode(Checkout|10): implement 0" in request
    )
    assert prompts == [["_manifest.tsv", "src_Basket.cs"]]
    assert (
        not (rig.repo.path / "bin" / "review_diff").exists() and rig.repo.status() == ""
    )


def test_a_refinement_that_changes_nothing_passes_without_a_commit(
    tmp_path: Path,
) -> None:
    """Given a chore agent that finds nothing to refine, when the chore runs, then no commit is made and the Event says so."""
    rig, head = delivered(tmp_path)
    script = DeveloperScript(
        rig.repo,
        [answers("completed", commit_message="", summary="Nothing to refine.")],
    )

    result = rig.run(refinement(rig, script))

    assert result.status == "completed" and rig.repo.head() == head
    assert rig.history(ChoreRefined) == (
        ChoreRefined("Nothing to refine.", (), "", committed=False),
    )


def test_a_target_that_does_not_refine_skips_the_chore(tmp_path: Path) -> None:
    """Given a routed target with the chore disabled, when the chore Stage runs, then it is skipped, the agent never runs and no Event is made."""
    rig, head = delivered(tmp_path)
    rig.target = replace(rig.target, chore=False)
    script = DeveloperScript(rig.repo, [refines()])

    result = rig.run(refinement(rig, script))

    assert result.status == "completed" and rig.skipped() == ["refine-chore"]
    assert script.requests == [] and rig.repo.head() == head
    assert rig.history(ChoreRefined) == () and rig.history(ChoreDiscarded) == ()


def test_a_branch_without_a_pull_request_skips_the_chore(tmp_path: Path) -> None:
    """Given a feature branch with nothing published, when the chore Stage runs, then it is skipped."""
    rig = StageRig(tmp_path)
    script = DeveloperScript(rig.repo, [refines()])

    result = rig.run(refinement(rig, script))

    assert (
        result.status == "completed"
        and rig.skipped() == ["refine-chore"]
        and script.requests == []
    )


def test_a_declined_chore_fails_with_a_chore_discarded_event_and_restores_the_worktree(
    tmp_path: Path,
) -> None:
    """Given a chore agent that edits and then declines, when the chore runs, then it fails, records the discard and leaves the branch as it was."""
    rig, head = delivered(tmp_path)

    def declines(repo: Repo) -> str:
        repo.write("half.txt", "half\n")
        return answers("failed", reason="cannot refine this")(repo)

    result = rig.run(refinement(rig, DeveloperScript(rig.repo, [declines])))

    assert result.status == "failed"
    assert rig.history(ChoreDiscarded) == (ChoreDiscarded("cannot refine this"),)
    assert rig.repo.head() == head and rig.repo.status() == ""


def test_a_malformed_reply_fails_with_a_chore_discarded_event_and_restores_the_worktree(
    tmp_path: Path,
) -> None:
    """Given a chore agent whose output has no status envelope, when the chore runs, then the chore is discarded and the worktree restored."""
    rig, head = delivered(tmp_path)

    def rambles(repo: Repo) -> str:
        repo.write("half.txt", "half\n")
        return "I changed things but forgot the envelope"

    result = rig.run(refinement(rig, DeveloperScript(rig.repo, [rambles])))

    assert result.status == "failed"
    (discarded,) = rig.history(ChoreDiscarded)
    assert "no response object in agent output" in discarded.reason
    assert rig.repo.head() == head and rig.repo.status() == ""


def test_a_refinement_that_fails_verification_is_discarded_and_nothing_is_committed(
    tmp_path: Path,
) -> None:
    """Given a refinement the verification rejects, when the chore runs, then the chore is discarded and the worktree restored."""
    rig, head = delivered(tmp_path)

    def verify(path: Path) -> Fail[ChoreDiscarded] | None:
        return Fail("the tests fail") if (path / "form.txt").exists() else None

    result = rig.run(
        refinement(rig, DeveloperScript(rig.repo, [refines()]), verify=verify)
    )

    assert result.status == "failed"
    assert rig.history(ChoreDiscarded) == (ChoreDiscarded("the tests fail"),)
    assert rig.history(ChoreRefined) == ()
    assert rig.repo.head() == head and rig.repo.status() == ""


def test_a_refinement_that_passes_verification_is_committed(tmp_path: Path) -> None:
    """Given a refinement the verification accepts, when the chore runs, then the verification saw the edit and the runtime committed it."""
    rig, head = delivered(tmp_path)
    seen: list[bool] = []

    def verify(path: Path) -> None:
        seen.append((path / "form.txt").exists())

    result = rig.run(
        refinement(rig, DeveloperScript(rig.repo, [refines()]), verify=verify)
    )

    assert result.status == "completed" and seen == [True]
    assert rig.repo.subjects()[0] == SUBJECT and rig.repo.head() != head


def test_an_agent_that_commits_itself_is_discarded_and_the_branch_restored(
    tmp_path: Path,
) -> None:
    """Given a chore agent that commits, when the chore runs, then only the runtime may commit so the chore is discarded and the branch restored."""
    rig, head = delivered(tmp_path)

    def commits(repo: Repo) -> str:
        repo.write("form.txt", "tidier\n")
        repo.commit("sneaky commit")
        return answers("completed", commit_message="tidy", summary="s")(repo)

    result = rig.run(refinement(rig, DeveloperScript(rig.repo, [commits])))

    assert result.status == "failed"
    (discarded,) = rig.history(ChoreDiscarded)
    assert "only the runtime commits" in discarded.reason
    assert rig.repo.head() == head and rig.repo.status() == ""


@pytest.mark.parametrize(
    ("proposed", "expected"),
    [
        ("tidy the form", "chore(Checkout): tidy the form"),
        (
            "chore(Checkout): tidy the form\n\nlong body",
            "chore(Checkout): tidy the form",
        ),
        ("", "chore(Checkout): refine the change set"),
        ("x" * 200, ("chore(Checkout): " + "x" * 200)[:100]),
    ],
    ids=["plain", "repeated-prefix-and-body", "empty", "capped"],
)
def test_the_default_chore_subject_prefixes_the_first_line_and_caps_it(
    proposed: str, expected: str
) -> None:
    """Given a proposed message, when the default subject is built, then it is prefixed once, one line and at most 100 characters."""
    assert chore.chore_subject("Checkout", proposed) == expected


def test_the_default_subject_is_used_when_the_workflow_gives_none(
    tmp_path: Path,
) -> None:
    """Given no subject parameter, when the chore commits, then the commit subject is the default `chore(<initiative>): <message>`."""
    rig, _ = delivered(tmp_path)

    rig.run(rig.in_worktree(DeveloperScript(rig.repo, [refines()]), chore.refine()))

    assert rig.repo.subjects()[0] == "chore(Checkout): tidy the form"


def test_the_pull_request_diff_is_split_per_file_with_a_manifest_of_real_paths(
    tmp_path: Path,
) -> None:
    """Given a pull request diff touching two paths that flatten to one name, when it is captured, then each gets a unique file and the manifest maps them."""
    diff = PR_DIFF + "diff --git a/src_Basket.cs b/src_Basket.cs\n+other\n"

    directory = chore.capture_pull_request_diff(diff, tmp_path)

    manifest = (directory / "_manifest.tsv").read_text().splitlines()
    assert manifest == [
        "src/Basket.cs\tsrc_Basket.cs",
        "src_Basket.cs\t2_src_Basket.cs",
    ]
    assert (
        (directory / "src_Basket.cs")
        .read_text()
        .startswith("diff --git a/src/Basket.cs")
    )

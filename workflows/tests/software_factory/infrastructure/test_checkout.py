from __future__ import annotations

from pathlib import Path

import pytest
from loop.shared.git import Commit, GitClient
from loop.shared.process import CommandError, CommandRunner

SEP_F, SEP_R = "\x1f", "\x1e"


class Script:
    """A fake `run` that answers a command by its git subcommand and records every call."""

    def __init__(self, **outputs: str | CommandError) -> None:
        self.outputs = outputs
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []

    def __call__(self, command, *, cwd=None, timeout_s=None) -> str:
        self.calls.append((tuple(command), cwd))
        result = self.outputs.get(command[1], "")
        if isinstance(result, CommandError):
            raise result
        return result


def _log(*commits: tuple[str, str]) -> str:
    return "".join(f"{sha}{SEP_F}{subject}{SEP_R}\n" for sha, subject in commits)


def test_head_and_commits_since_parse_the_log_oldest_first() -> None:
    script = Script(log=_log(("a1", "first"), ("b2", "second: body")))
    git = GitClient(script)

    assert git.head(Path("/w")) == Commit("a1", "first")
    assert git.commits_since(Path("/w"), "z9") == [
        Commit("a1", "first"),
        Commit("b2", "second: body"),
    ]
    assert "--reverse" in script.calls[1][0] and "z9..HEAD" in script.calls[1][0]


def test_initiative_commits_drop_commits_matching_only_in_the_body() -> None:
    git = GitClient(Script(log=_log(("a1", "ccode(X|1): work"), ("b2", "other"))))

    assert git.initiative_commits(Path("/w"), "main", "ccode(X|") == [
        Commit("a1", "ccode(X|1): work")
    ]


def test_push_if_ahead_pushes_a_local_branch_with_commits_beyond_its_upstream() -> None:
    script = Script(**{"rev-list": "c3\n"})
    assert GitClient(script).push_if_ahead(Path("/w"), "feat", "main") is True
    assert script.calls[-1][0] == ("git", "push", "origin", "feat")
    assert ("git", "rev-list", "origin/feat..feat") in [
        call[0] for call in script.calls
    ]


def test_push_if_ahead_does_nothing_when_not_ahead_or_branch_missing() -> None:
    script = Script(**{"rev-list": ""})
    assert GitClient(script).push_if_ahead(Path("/w"), "feat", "main") is False
    assert all(call[0][1] != "push" for call in script.calls)

    missing = Script(**{"show-ref": CommandError("git", 1, "")})
    assert GitClient(missing).push_if_ahead(Path("/w"), "feat", "main") is False


def test_remote_branch_exists_reports_the_show_ref_outcome() -> None:
    assert GitClient(Script()).remote_branch_exists(Path("/w"), "main") is True
    assert (
        GitClient(
            Script(**{"show-ref": CommandError("git", 1, "")})
        ).remote_branch_exists(Path("/w"), "m")
        is False
    )


def test_restore_resets_hard_and_cleans_untracked_files() -> None:
    script = Script()

    GitClient(script).restore(Path("/w"), "abc")

    assert [call[0] for call in script.calls] == [
        ("git", "reset", "--hard", "abc"),
        ("git", "clean", "-fd"),
    ]


def test_is_clean_follows_porcelain_status() -> None:
    assert GitClient(Script(status="")).is_clean(Path("/w")) is True
    assert GitClient(Script(status=" M a.py\n")).is_clean(Path("/w")) is False


def test_command_runner_returns_stdout_and_raises_command_error_on_failure() -> None:
    assert CommandRunner()(["python", "-c", "print('hi')"]).strip() == "hi"
    with pytest.raises(CommandError):
        CommandRunner()(["python", "-c", "import sys; sys.exit(3)"])
    with pytest.raises(CommandError):
        CommandRunner()(["definitely-not-a-binary-xyz"])


def test_fix_commits_stage_edits_and_preserve_their_explanation() -> None:
    """Given verifier edits, when the harness commits, then it stages them and retains the body."""
    script = Script(
        log=f"fix1{SEP_F}fix(Basket): total{SEP_F}Verified the basket total.{SEP_R}\n"
    )
    commit = GitClient(script).commit(
        Path("/w"), "fix(Basket): total", "Verified the basket total."
    )
    assert commit == Commit("fix1", "fix(Basket): total", "Verified the basket total.")
    assert script.calls[0] == (("git", "add", "-A"), Path("/w"))
    assert script.calls[1][0] == (
        "git",
        "commit",
        "-m",
        "fix(Basket): total",
        "-m",
        "Verified the basket total.",
    )


def test_changed_files_include_untracked_files_and_rename_destinations() -> None:
    """Given edited and untracked files, when validating fixes, then all resulting paths are checked."""
    script = Script(
        status=" M src/Basket.cs\n?? proof/new.json\nR  old.cs -> src/New.cs\n"
    )
    assert GitClient(script).changed_files(Path("/w")) == [
        "src/Basket.cs",
        "proof/new.json",
        "src/New.cs",
    ]

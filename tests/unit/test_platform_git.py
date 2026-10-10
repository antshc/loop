from __future__ import annotations

from pathlib import Path

import pytest

from workflows.platforms.git import Commit, WorkflowGit
from workflows.platforms.process import CommandError, run_command

SEP_F, SEP_R = "\x1f", "\x1e"


class Script:
    """A fake `run` that answers a command by its git subcommand and records every call."""

    def __init__(self, **outputs: str | CommandError) -> None:
        self.outputs = outputs
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []

    def __call__(self, command, *, cwd=None) -> str:
        self.calls.append((tuple(command), cwd))
        result = self.outputs.get(command[1], "")
        if isinstance(result, CommandError):
            raise result
        return result


def _log(*commits: tuple[str, str]) -> str:
    return "".join(f"{sha}{SEP_F}{subject}{SEP_R}\n" for sha, subject in commits)


def test_head_and_commits_since_parse_the_log_oldest_first() -> None:
    script = Script(log=_log(("a1", "first"), ("b2", "second: body")))
    git = WorkflowGit(run=script)

    assert git.head(Path("/w")) == Commit("a1", "first")
    assert git.commits_since(Path("/w"), "z9") == [Commit("a1", "first"), Commit("b2", "second: body")]
    assert "--reverse" in script.calls[1][0] and "z9..HEAD" in script.calls[1][0]


def test_initiative_commits_drop_commits_matching_only_in_the_body() -> None:
    git = WorkflowGit(run=Script(log=_log(("a1", "ccode(X|1): work"), ("b2", "other"))))

    assert git.initiative_commits(Path("/w"), "main", "ccode(X|") == [Commit("a1", "ccode(X|1): work")]


def test_push_if_ahead_pushes_a_local_branch_with_commits_beyond_its_upstream() -> None:
    script = Script(**{"rev-list": "c3\n"})
    assert WorkflowGit(run=script).push_if_ahead(Path("/w"), "feat", "main") is True
    assert script.calls[-1][0] == ("git", "push", "origin", "feat")
    assert ("git", "rev-list", "origin/feat..feat") in [call[0] for call in script.calls]


def test_push_if_ahead_does_nothing_when_not_ahead_or_branch_missing() -> None:
    script = Script(**{"rev-list": ""})
    assert WorkflowGit(run=script).push_if_ahead(Path("/w"), "feat", "main") is False
    assert all(call[0][1] != "push" for call in script.calls)

    missing = Script(**{"show-ref": CommandError("git", 1, "")})
    assert WorkflowGit(run=missing).push_if_ahead(Path("/w"), "feat", "main") is False


def test_remote_branch_exists_reports_the_show_ref_outcome() -> None:
    assert WorkflowGit(run=Script()).remote_branch_exists(Path("/w"), "main") is True
    assert WorkflowGit(run=Script(**{"show-ref": CommandError("git", 1, "")})).remote_branch_exists(Path("/w"), "m") is False


def test_restore_resets_hard_and_cleans_untracked_files() -> None:
    script = Script()

    WorkflowGit(run=script).restore(Path("/w"), "abc")

    assert [call[0] for call in script.calls] == [("git", "reset", "--hard", "abc"), ("git", "clean", "-fd")]


def test_is_clean_follows_porcelain_status() -> None:
    assert WorkflowGit(run=Script(status="")).is_clean(Path("/w")) is True
    assert WorkflowGit(run=Script(status=" M a.py\n")).is_clean(Path("/w")) is False


def test_run_command_returns_stdout_and_raises_command_error_on_failure() -> None:
    assert run_command(["python", "-c", "print('hi')"]).strip() == "hi"
    with pytest.raises(CommandError):
        run_command(["python", "-c", "import sys; sys.exit(3)"])
    with pytest.raises(CommandError):
        run_command(["definitely-not-a-binary-xyz"])

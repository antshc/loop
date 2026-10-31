"""address_once scenarios through the command line: what one run does with trusted, untrusted and no feedback over fake GitHub, a real repository and a scripted agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflows.software_factory.events.feedback import FeedbackHandled
from tests.runtime.harness import command
from tests.shared.fakes import comment_node, thread_node
from workflows.tests.software_factory.stages.pr_support import reply_text
from workflows.software_factory.workflows.address_once import __main__ as entry

from .harness import FAILS_AFTER_EDIT, URL, Addressing

KEY = "thread:t1:t1c"


def test_help_exits_0_and_lists_the_config_and_pr_options(capsys: pytest.CaptureFixture[str]) -> None:
    """Given the --help option, when the command starts, then it exits 0 and lists every option."""
    with pytest.raises(SystemExit) as exit_info:
        entry.main(["--help"])

    help_text = capsys.readouterr().out
    assert exit_info.value.code == 0 and all(option in help_text for option in ("--config", "--pr"))


def test_a_pr_that_is_no_github_url_is_refused_with_a_usage_error(tmp_path: Path) -> None:
    """Given a --pr that is no GitHub URL, when the command starts, then it exits with a usage error."""
    with pytest.raises(SystemExit) as exit_info:
        entry.main(["--config", str(tmp_path / "missing.json"), "--pr", "not-a-url"])

    assert exit_info.value.code == 2


def test_trusted_feedback_is_addressed_verified_committed_pushed_and_replied_to_once_and_the_run_exits_0(tmp_path: Path) -> None:
    """Given a trusted review thread, when the pull request is addressed, then the issue is fixed in a commit that is pushed, the reply is posted once and the run completes with exit 0."""
    rig = Addressing(tmp_path)

    code = rig.address()

    assert code == 0 and rig.status().status == "completed"
    assert rig.agent.modes() == ["group", "resolve:i1"]
    assert rig.replies() == [reply_text(KEY)]
    assert rig.commits() == ["address_once: address i1 (PR #7)"] and rig.pushed()
    assert rig.remote_head("feature") == command(("rev-parse", rig.recovery_branch()), rig.repo.path).strip()
    assert rig.history(FeedbackHandled)[0].keys == (KEY,)
    assert not rig.worktree().exists()


def test_a_second_run_finds_the_feedback_handled_and_changes_nothing(tmp_path: Path) -> None:
    """Given feedback that was addressed, when the pull request is addressed again, then the run holds, exits 0 and posts nothing more."""
    rig = Addressing(tmp_path)
    assert rig.address() == 0
    pushed = rig.remote_head("feature")

    code = rig.address()

    assert code == 0 and rig.status().status == "held"
    assert rig.replies() == [reply_text(KEY)] and rig.remote_head("feature") == pushed and rig.agent.modes() == ["group", "resolve:i1"]


def test_a_pull_request_without_feedback_holds_and_runs_no_agent(tmp_path: Path) -> None:
    """Given a pull request without review feedback, when it is addressed, then the run holds, exits 0 and no agent ran."""
    rig = Addressing(tmp_path, threads=[])

    code = rig.address()

    assert code == 0 and rig.status().status == "held" and rig.agent.modes() == []
    assert rig.replies() == [] and not rig.pushed()


def test_feedback_of_an_untrusted_author_is_not_addressed(tmp_path: Path) -> None:
    """Given a thread by an outsider, when the pull request is addressed, then the run holds and nothing is changed or replied to."""
    outsider = thread_node([comment_node("t1c", "outsider", "drive-by", association="NONE")], id="t1")
    rig = Addressing(tmp_path, threads=[outsider])

    code = rig.address()

    assert code == 0 and rig.status().status == "held" and rig.agent.modes() == []
    assert rig.replies() == [] and not rig.pushed()


def test_a_failing_check_keeps_the_edits_hands_the_run_off_and_commits_pushes_and_replies_to_nothing(tmp_path: Path) -> None:
    """Given a repository whose tests fail once the fix edits a file, when the pull request is addressed, then the edits stay in the recovery worktree, the run is closed handed off with exit 1 and nothing is committed, pushed or replied to."""
    rig = Addressing(tmp_path, test=FAILS_AFTER_EDIT)

    code = rig.address()

    status = rig.status()
    assert code == 1 and status.status == "handed_off" and "preserved" in (status.reason or "")
    assert (rig.worktree() / "cart.py").read_text() == "cart.py\n"
    assert rig.commits() == [] and not rig.pushed() and rig.replies() == []


def test_a_pull_request_of_an_unconfigured_repository_exits_1_before_any_git_operation(tmp_path: Path) -> None:
    """Given a pull request whose repository is not configured, when the command runs, then it exits 1 before any Git operation."""
    rig = Addressing(tmp_path)

    code = entry.main(["--config", str(rig.config), "--pr", "https://github.com/c/d/pull/1"], services=rig.services)

    assert code == 1 and rig.runner.calls == []


def test_a_missing_configuration_file_exits_1(tmp_path: Path) -> None:
    """Given a configuration file that does not exist, when the command runs, then it exits 1."""
    assert entry.main(["--config", str(tmp_path / "missing.json"), "--pr", URL]) == 1


def test_a_repository_without_a_test_command_is_refused(tmp_path: Path) -> None:
    """Given a configured repository without a test command, when the command runs, then it exits 1."""
    config = tmp_path / "address_once.json"
    config.write_text('{"repositories": [{"name": "a/b", "path": ".", "build": ["make"]}]}')

    assert entry.main(["--config", str(config), "--pr", "https://github.com/a/b/pull/1"]) == 1


def test_the_real_services_are_exactly_what_the_definition_needs() -> None:
    """Given the real services, when they are built, then they are Git, the runtime's Git, GitHub and the agent launcher."""
    names = sorted(type_.__name__ for type_ in entry.build_services().types())

    assert names == ["AgentLauncher", "GitClient", "GitHub", "WorktreeGit"]

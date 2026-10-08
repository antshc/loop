from __future__ import annotations

import ast
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from loop import (
    AgentClient,
    CommandError,
    CopilotClient,
    FileExecutionStore,
    Hook,
    InMemoryExecutionStore,
)
from loop.testing import FakeAgentClient, FakeCopilotCli, FakeGit
from workflows import dev
from workflows.platforms.work_tracking import GitHubClient, RepositoryConfig, Spec, TicketsTracker
from workflows.platforms.work_tracking.fake_gh_cli import FakeGhCli

from workflow_harness import (
    DevHarness,
    _commit_and_report,
    _commit_then,
    _completed,
    _COMPLETED,
    _envelope,
    _issue,
    _make_repo,
    _only_worktree,
    _writes,
)

SRC = Path(__file__).parents[2] / "src" / "loop"
WORKFLOWS = Path(__file__).parents[2] / "workflows"
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


# --- Unit tests: dev result model, branch-name rule, metadata parsing ---------------


def test_parse_dev_result_decodes_a_completed_response() -> None:
    result = dev.parse_dev_result(_envelope(result=_COMPLETED))

    assert result == dev.DevResult("Checkout|10", "completed", commit="abc", summary="done", verification="ran tests")


def test_parse_dev_result_decodes_a_failed_response() -> None:
    result = dev.parse_dev_result(_envelope(status="failed", result={"reason": "stuck"}))

    assert (result.status, result.reason) == ("failed", "stuck")


@pytest.mark.parametrize(
    "response",
    [
        "{not json",
        "[1]",
        json.dumps({"identifier": "x", "status": "weird", "result": {}}),
        json.dumps({"identifier": "x", "status": "completed", "result": {"commit": "a", "summary": "s"}}),
        json.dumps({"identifier": "x", "status": "failed", "result": {}}),
    ],
)
def test_parse_dev_result_rejects_a_malformed_response(response: str) -> None:
    with pytest.raises(dev.DevResultError):
        dev.parse_dev_result(response)


def _spec(*, base_branch: str, title: str) -> Spec:
    return Spec(1, title, "url", (f"repo:base:{base_branch}",))


def test_feature_branch_prefixes_the_slug_with_an_underscored_version() -> None:
    assert _spec(base_branch="release/2.4", title="Add Login Page!").feature_branch == "2_4_add-login-page"


def test_feature_branch_is_just_the_slug_without_a_version() -> None:
    assert _spec(base_branch="main", title="Add Login Page!").feature_branch == "add-login-page"


def _first_ticket():
    tracker = TicketsTracker(GitHubClient("o", "r", gh=FakeGhCli()))
    return next(iter(tracker.specs())).tickets[0]


def test_prompt_template_placeholders_match_the_supplied_arguments_exactly() -> None:
    args = dev.prompt_args(_first_ticket(), dev.WorkIdentifier("Checkout", 10), ["abc1234 first"], Path("/w"), "main", "feature")

    placeholders = set(_PLACEHOLDER.findall(dev.PROMPT.read_text()))

    assert placeholders == set(args.keys())


def test_prompt_args_carry_only_the_ticket_its_task_id_and_the_initiative_commits() -> None:
    args = dev.prompt_args(_first_ticket(), dev.WorkIdentifier("Checkout", 10), [], Path("/w"), "main", "feature")

    ticket_json = json.loads(args["TICKET_JSON"])
    assert (ticket_json["number"], ticket_json["body"]) == (10, "Body of #10")
    assert ticket_json["comments"] == [{"author": "alice", "body": "Comment on #10", "created_at": "2026-01-01T00:00:00Z"}]
    assert args["TASK_ID"] == "Checkout|10"
    assert "No task commits" in args["INITIATIVE_COMMITS"]
    assert {"SPEC_JSON", "TICKETS_JSON", "RECENT_COMMITS"}.isdisjoint(args)


# --- Functional tests: drive dev.main with fakes at every process boundary --------------------------------


def test_agents_run_on_the_harness_root_while_the_prompt_carries_the_worktree(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    workspaces: list[str] = []

    def agent_factory(binding):
        workspaces.append(binding.workspace)

        def handler(prompt, options):
            harness.agent_calls.append((prompt, options))
            return _commit_and_report(harness, 10)

        return FakeAgentClient(handler)

    code = harness.run(agent_factory=agent_factory)

    assert code == 0
    assert workspaces == [str(harness.harness_root)]
    worktree = harness.git.removed[-1]
    assert worktree != harness.harness_root
    assert str(worktree) in harness.agent_calls[0][0]


def test_no_arguments_use_the_current_folder_as_the_harness_root_and_its_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path, "git@github.com:owner/repo.git")
    monkeypatch.chdir(tmp_path)
    gh = FakeGhCli(specs=[], tickets={})
    github = GitHubClient("owner", "repo", gh=gh)

    code = dev.main(
        [],
        github_factory=lambda checkout: github,
        repositories=[RepositoryConfig(path=tmp_path, owner_repo="owner/repo", is_harness=True)],
        store=InMemoryExecutionStore(),
    )

    assert code == 0
    assert (tmp_path / ".loop" / "dev.log").is_file()


def test_log_dir_override_writes_logs_to_that_folder(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, specs=[], tickets={})
    custom = tmp_path / "custom-logs"

    code = harness.run(log_dir=custom)

    assert code == 0
    assert (custom / "dev.log").is_file()
    assert not (harness.harness_root / ".loop").exists()


def test_exits_before_reading_specs_when_no_repository_is_configured_as_the_harness(tmp_path: Path) -> None:
    gh = FakeGhCli()

    code = dev.main(
        ["--harness-root", str(tmp_path), "--log-dir", str(tmp_path / "logs")],
        github_factory=lambda checkout: GitHubClient("owner", "repo", gh=gh),
        repositories=[RepositoryConfig(path=tmp_path, owner_repo="owner/repo")],
        store=InMemoryExecutionStore(),
    )

    assert code == 1
    assert gh.calls == []


def test_exits_before_reading_specs_when_more_than_one_repository_is_configured_as_the_harness(
    tmp_path: Path,
) -> None:
    gh = FakeGhCli()

    code = dev.main(
        ["--harness-root", str(tmp_path), "--log-dir", str(tmp_path / "logs")],
        github_factory=lambda checkout: GitHubClient("owner", "repo", gh=gh),
        repositories=[
            RepositoryConfig(path=tmp_path, owner_repo="owner/repo", is_harness=True),
            RepositoryConfig(path=tmp_path / "other", owner_repo="owner/other", is_harness=True),
        ],
        store=InMemoryExecutionStore(),
    )

    assert code == 1
    assert gh.calls == []


def test_a_plain_folder_can_be_the_harness_root_when_configured_in_the_pool(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    gh = FakeGhCli(specs=[], tickets={})

    code = dev.main(
        ["--harness-root", str(root), "--log-dir", str(tmp_path / "logs")],
        github_factory=lambda checkout: GitHubClient("owner", "repo", gh=gh),
        repositories=[RepositoryConfig(path=root, owner_repo="owner/repo", is_harness=True)],
        store=InMemoryExecutionStore(),
    )

    assert code == 0


def test_an_unexpected_error_is_logged_and_the_process_exits_non_zero(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    class RaisingGithub:
        def spec_issues(self):
            raise RuntimeError("boom")

    code = harness.run(github_factory=lambda checkout: RaisingGithub())

    assert code == 1
    assert "boom" in (harness.log_dir / "dev.log").read_text()


def test_worktree_is_created_from_the_harness_when_the_spec_targets_its_origin(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run(handler=lambda prompt, options: _commit_and_report(harness, 10))

    assert code == 0
    assert harness.git.fetched == [harness.harness_root]


def test_worktree_is_created_from_the_workspace_clone_and_pr_goes_to_the_target_repo(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[_issue(1, "Add a widget", labels=("spec", "repo:target:acme/widgets", "repo:base:main"))],
        tickets={1: [_issue(10, "Build the widget")]},
    )
    clone = _make_repo(harness.harness_root / "workspace" / "widgets", "git@github.com:acme/widgets.git")
    harness.git.remote_branches.add("main")
    target_gh = FakeGhCli(specs=[], tickets={})
    target_github = GitHubClient("acme", "widgets", gh=target_gh)

    def github_factory(checkout: Path):
        return harness.github if checkout == harness.harness_root else target_github

    code = harness.run(
        handler=lambda prompt, options: _commit_and_report(harness, 10, initiative="1"),
        github_factory=github_factory,
        repositories=[
            RepositoryConfig(path=harness.harness_root, owner_repo="owner/repo", is_harness=True),
            RepositoryConfig(path=clone, owner_repo="acme/widgets"),
        ],
    )

    assert code == 0
    assert harness.git.fetched == [clone]
    assert any(call[:2] == ("pr", "create") for call in target_gh.calls)
    assert any(call[:2] == ("issue", "close") for call in harness.gh.calls)


def test_unconfigured_repo_target_is_labelled_hitl_with_no_worktree_created(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[_issue(1, "Add a widget", labels=("spec", "repo:target:acme/widgets", "repo:base:main"))],
        tickets={1: [_issue(10, "Build the widget")]},
    )

    code = harness.run()

    assert code == 0
    assert harness.git.worktree_branches == {} and harness.git.fetched == []
    labels = [c for c in harness.gh.calls if c[:2] == ("issue", "edit")]
    comments = [c for c in harness.gh.calls if c[:2] == ("issue", "comment")]
    assert labels and labels[0][-1] == "hitl"
    assert comments and "repo:target:acme/widgets" in comments[0][-1] and "RepositoryPool" in comments[0][-1]


@pytest.mark.parametrize(
    "labels",
    [
        ("spec", "repo:base:main"),
        ("spec", "repo:target:not-a-slug", "repo:base:main"),
        ("spec", "repo:target:owner/repo", "repo:target:other/one", "repo:base:main"),
    ],
)
def test_bad_target_label_is_labelled_hitl_and_never_falls_back_to_the_harness(
    tmp_path: Path, labels: tuple[str, ...]
) -> None:
    harness = DevHarness(
        tmp_path, specs=[_issue(1, "Add login page", labels=labels)], tickets={1: [_issue(10, "Add login form")]}
    )

    code = harness.run()

    assert code == 0
    assert harness.git.fetched == []
    edits = [c for c in harness.gh.calls if c[:2] == ("issue", "edit")]
    assert edits and edits[0][-1] == "hitl"


def test_a_spec_labelled_hitl_is_skipped(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[_issue(1, "Add login page", labels=("spec", "hitl", "repo:target:owner/repo", "repo:base:main"))],
        tickets={1: [_issue(10, "Add login form")]},
    )

    code = harness.run()

    assert code == 0
    assert _writes(harness.gh) == []


def test_a_ticket_that_failed_once_in_an_earlier_run_is_escalated_on_its_next_failure_without_a_retry(
    tmp_path: Path,
) -> None:
    harness = DevHarness(tmp_path)
    harness.store.record_failure("https://github.com/owner/repo/issues/10", owner="owner", repo="repo", task_id="10", title="x", items=[10])

    code = harness.run(handler=lambda prompt, options: "")

    assert code == 1
    assert len(harness.agent_calls) == 1
    assert {c[2] for c in harness.gh.calls if c[:2] == ("issue", "edit") and c[-1] == "hitl"} == {"1", "10"}


def test_failures_persist_per_ticket_in_the_days_execution_log(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    store = FileExecutionStore(harness.log_dir, clock=lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

    code = harness.run(handler=lambda prompt, options: "", store=store)

    assert code == 1
    records = json.loads((harness.log_dir / "dev-execution-log-2026-01-01.json").read_text())
    assert (records[0]["count"], records[0]["last_run"], records[0]["last_items"]) == (2, "2026-01-01T12:00:00Z", [10])


def test_a_first_failure_retries_the_same_ticket_immediately_without_hitl(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    calls: list[int] = []

    def handler(prompt, options):
        calls.append(1)
        return "" if len(calls) == 1 else _commit_and_report(harness, 10)

    code = harness.run(handler=handler)

    assert code == 0
    assert len(harness.agent_calls) == 2 and all(o.session_key is None for _, o in harness.agent_calls)
    assert all(c[:2] != ("issue", "edit") for c in harness.gh.calls)
    assert [c[2] for c in harness.gh.calls if c[:2] == ("issue", "close")] == ["10"]
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/10") == 0


def test_two_tickets_each_failing_once_are_not_escalated(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: [_issue(10, "Add login form"), _issue(11, "Add login tests")]})
    seen: set[str] = set()

    def handler(prompt, options):
        number = 10 if "Task id: `Checkout|10`" in prompt else 11
        if number not in seen:
            seen.add(number)
            return ""
        return _commit_and_report(harness, number)

    code = harness.run(handler=handler)

    assert code == 0
    assert all(c[:2] != ("issue", "edit") for c in harness.gh.calls)
    assert [c[2] for c in harness.gh.calls if c[:2] == ("issue", "close")] == ["10", "11"]


def test_remote_target_branch_missing_is_labelled_hitl(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.remote_branches.discard("main")

    code = harness.run()

    assert code == 0
    edits = [c for c in harness.gh.calls if c[:2] == ("issue", "edit")]
    assert edits and edits[0][-1] == "hitl"
    assert harness.git.worktree_branches == {}


def test_worktree_branch_name_follows_the_versioned_or_plain_slug_rule(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[
            _issue(1, "Checkout: Add login page", labels=("spec", "repo:target:owner/repo", "repo:base:release/2.4"))
        ],
        tickets={1: [_issue(10, "Add login form")]},
    )
    harness.git.remote_branches.add("release/2.4")

    code = harness.run()

    assert code == 1
    assert "2_4_add-login-page" in harness.git.branch_commits


def test_a_dirty_leftover_worktree_fails_the_attempt_and_is_left_in_place(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    leftover = harness.harness_root / "workspace" / "harness.worktrees" / "add-login-page"
    harness.git.dirty_leftovers.add(leftover)

    code = harness.run()

    assert code == 1
    comments = [c for c in harness.gh.calls if c[:2] == ("issue", "comment")]
    assert comments and str(leftover) in comments[0][-1]
    edits = [c for c in harness.gh.calls if c[:2] == ("issue", "edit")]
    assert edits == []


def test_a_failing_worktree_ready_hook_removes_the_worktree_and_fails_the_attempt(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.failing_hooks.add("setup.sh")

    code = harness.run(hooks=(Hook("setup.sh"),))

    assert code == 1
    assert harness.git.worktree_branches == {}
    assert len(harness.git.removed) == 1
    assert harness.agent_calls == []


def test_a_cancelled_hook_on_a_clean_worktree_is_reported_cancelled_and_removes_it(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.cancelled_hooks.add("setup.sh")

    code = harness.run(hooks=(Hook("setup.sh"),))

    assert code == 0
    assert harness.git.worktree_branches == {}
    assert len(harness.git.removed) == 1
    assert _writes(harness.gh) == []
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/1") == 0


def test_a_cancelled_hook_on_a_dirty_worktree_keeps_it_with_no_publication_or_label(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.cancelled_hooks.add("setup.sh")
    harness.git.branch_commits["add-login-page"] = ["already-dirty"]
    harness.git.remote_branches.add("add-login-page")
    harness.git.remote_heads["add-login-page"] = "already-dirty"

    code = harness.run(hooks=(Hook("setup.sh"),))

    assert code == 0
    assert harness.git.worktree_branches
    assert harness.git.removed == []
    assert _writes(harness.gh) == []
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/1") == 0


def test_a_cancelled_agent_run_with_no_changes_is_reported_cancelled_and_removes_the_worktree(
    tmp_path: Path,
) -> None:
    harness = DevHarness(tmp_path)
    cancel = threading.Event()

    def handler(prompt: str, options: object) -> str:
        cancel.set()
        return ""

    code = harness.run(handler=handler, cancel=cancel)

    assert code == 0
    assert harness.git.worktree_branches == {}
    assert len(harness.git.removed) == 1
    assert _writes(harness.gh) == []
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/1") == 0


def test_a_cancelled_agent_run_with_committed_changes_keeps_the_worktree_with_no_publication_or_label(
    tmp_path: Path,
) -> None:
    harness = DevHarness(tmp_path)
    cancel = threading.Event()

    def handler(prompt: str, options: object) -> str:
        harness.git.commit(_only_worktree(harness.git), "partial work")
        cancel.set()
        return ""

    code = harness.run(handler=handler, cancel=cancel)

    assert code == 0
    assert harness.git.worktree_branches
    assert harness.git.removed == []
    assert _writes(harness.gh) == []
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/1") == 0


def test_any_attempt_ending_removes_the_worktree_but_keeps_the_local_branch(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run()

    assert code == 1
    assert harness.git.worktree_branches == {}
    assert len(harness.git.removed) == 1
    assert "add-login-page" in harness.git.branch_commits


def test_a_prompt_placeholder_with_no_argument_fails_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad_template = tmp_path / "bad.md"
    bad_template.write_text("Spec {{NOT_A_REAL_KEY}}")
    monkeypatch.setattr(dev, "PROMPT", bad_template)
    harness = DevHarness(tmp_path)

    code = harness.run()

    assert code == 1
    assert harness.agent_calls == []


def test_every_complete_ticket_with_changes_pushes_prs_and_closes_with_sha_summary_and_verification(
    tmp_path: Path,
) -> None:
    harness = DevHarness(tmp_path)
    shas: list[str] = []

    def handler(prompt, options):
        response = _commit_and_report(harness, 10, summary="added the form")
        shas.append(json.loads(response)["result"]["commit"])
        return response

    code = harness.run(handler=handler)

    assert code == 0
    assert harness.git.pushed == [(harness.git.removed[0], "add-login-page")]
    closes = [c for c in harness.gh.calls if c[:2] == ("issue", "close")]
    assert closes[0][2] == "10"
    assert shas[0] in closes[0][-1] and "added the form" in closes[0][-1] and "ran tests" in closes[0][-1]
    assert len([c for c in harness.gh.calls if c[:2] == ("pr", "create")]) == 1


def test_python_pushes_the_agents_commit_without_committing_itself(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run(handler=lambda prompt, options: _commit_and_report(harness, 10))

    assert code == 0
    assert list(harness.git.subjects.values()) == ["ccode(Checkout|10): work"]
    assert len(harness.git.pushed) == 1


def test_two_actionable_tickets_each_get_a_fresh_run_with_no_session_in_order(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: [_issue(10, "Add login form"), _issue(11, "Add login tests")]})
    numbers = iter((10, 11))

    code = harness.run(handler=lambda prompt, options: _commit_and_report(harness, next(numbers)))

    assert code == 0
    assert len(harness.agent_calls) == 2
    assert all(options.session_key is None for _, options in harness.agent_calls)
    closes = [c[2] for c in harness.gh.calls if c[:2] == ("issue", "close")]
    assert closes == ["10", "11"]


def test_a_tickets_prompt_excludes_the_spec_body_and_other_tickets(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: [_issue(10, "Add login form"), _issue(11, "Add login tests")]})

    harness.run(handler=lambda prompt, options: "")

    prompt = harness.agent_calls[0][0]
    assert "Body of #10" in prompt and "Checkout|10" in prompt and str(harness.git.removed[0]) in prompt
    assert "Body of #1" not in prompt.replace("Body of #10", "") and "Add login tests" not in prompt


def test_the_prompt_lists_only_this_initiatives_task_commits(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.branch_commits["add-login-page"] = ["c1", "c2", "c3"]
    harness.git.subjects.update(
        {"c1": "ccode(Checkout|9): earlier work", "c2": "ccode(Other|3): other initiative", "c3": "ccode: bare"}
    )

    harness.run(handler=lambda prompt, options: "")

    prompt = harness.agent_calls[0][0]
    assert "ccode(Checkout|9): earlier work" in prompt
    assert "other initiative" not in prompt and "bare" not in prompt


def test_the_prompt_states_when_the_initiative_has_no_task_commits(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    harness.run(handler=lambda prompt, options: "")

    assert "No task commits for this Initiative" in harness.agent_calls[0][0]


def test_all_tickets_delivered_pushes_one_draft_pr_and_comments_its_link_on_the_open_spec(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: [_issue(10, "Add login form"), _issue(11, "Add login tests")]})
    numbers = iter((10, 11))

    code = harness.run(handler=lambda prompt, options: _commit_and_report(harness, next(numbers)))

    assert code == 0
    assert len(harness.git.pushed) == 1
    assert len([c for c in harness.gh.calls if c[:2] == ("pr", "create")]) == 1
    spec_comments = [c for c in harness.gh.calls if c[:2] == ("issue", "comment") and c[2] == "1"]
    assert len(spec_comments) == 1 and "pull" in spec_comments[0][-1]
    assert all(c[2] != "1" for c in harness.gh.calls if c[:2] == ("issue", "close"))


def test_a_hitl_stop_still_pushes_the_commits_of_tickets_closed_in_the_run(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: [_issue(10, "Add login form"), _issue(11, "Add login tests")]})

    def handler(prompt, options):
        return _commit_and_report(harness, 10) if "Task id: `Checkout|10`" in prompt else ""

    code = harness.run(handler=handler)

    assert code == 1
    assert len(harness.git.pushed) == 1
    assert any(c[:2] == ("pr", "create") for c in harness.gh.calls)
    assert all("pull request" not in c[-1] for c in harness.gh.calls if c[:2] == ("issue", "comment") and c[2] == "1")


def test_a_branch_ahead_at_spec_start_is_pushed_from_the_checkout_before_the_worktree_is_created(
    tmp_path: Path,
) -> None:
    harness = DevHarness(tmp_path)
    harness.git.branch_commits["add-login-page"] = ["c1"]
    harness.git.subjects["c1"] = "ccode(Checkout|9): earlier"
    created_after_push: list[bool] = []
    original = harness.git.worktrees.create

    def spy(*args, **kwargs):
        created_after_push.append(bool(harness.git.pushed))
        return original(*args, **kwargs)

    harness.git.worktrees.create = spy

    harness.run(handler=lambda prompt, options: "")

    assert harness.git.pushed[0] == (harness.harness_root, "add-login-page")
    assert created_after_push == [True]
    assert harness.git.remote_heads["add-login-page"] == "c1"
    assert any(c[:2] == ("pr", "create") for c in harness.gh.calls)


def test_a_spec_with_no_actionable_ticket_and_a_branch_ahead_is_pushed_with_its_pr(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: []})
    harness.git.branch_commits["add-login-page"] = ["c1"]

    code = harness.run()

    assert code == 0
    assert harness.git.pushed == [(harness.harness_root, "add-login-page")]
    assert any(c[:2] == ("pr", "create") for c in harness.gh.calls)


def test_nothing_ahead_means_no_push_and_no_pull_request(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: []})

    code = harness.run()

    assert code == 0
    assert harness.git.pushed == [] and _writes(harness.gh) == []


def test_repeating_a_run_with_the_remote_up_to_date_pushes_nothing_more(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    harness.run(handler=lambda prompt, options: _commit_and_report(harness, 10))
    pushes = len(harness.git.pushed)
    harness.run(handler=lambda prompt, options: "")

    assert pushes == 1 and len(harness.git.pushed) == 1


def test_a_draft_pr_already_open_for_the_branch_is_not_duplicated(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        prs=[
            {
                "number": 5,
                "title": "existing",
                "url": "https://github.com/owner/repo/pull/5",
                "headRefName": "add-login-page",
            }
        ],
    )

    code = harness.run(handler=lambda prompt, options: _commit_and_report(harness, 10))

    assert code == 0
    assert all(c[:2] != ("pr", "create") for c in harness.gh.calls)


@pytest.mark.parametrize(
    ("handler_for", "reason"),
    [
        (lambda h: lambda p, o: _envelope(result=_COMPLETED), "HEAD did not change"),
        (lambda h: lambda p, o: "no json here", "no response object"),
        (lambda h: _commit_then(h, lambda c: _envelope("Checkout|11", result={**_COMPLETED, "commit": c})), "identifier"),
        (lambda h: _commit_then(h, lambda c: _completed("deadbeef")), "result.commit"),
        (lambda h: _commit_then(h, lambda c: _envelope(result={"commit": c, "summary": "s"})), "required"),
        (
            lambda h: lambda p, o: _commit_and_report(h, 10, subject="feat: wrong subject"),
            "does not start with",
        ),
        (lambda h: lambda p, o: (_ for _ in ()).throw(CommandError("copilot", None, "crashed")), "crashed"),
    ],
)
def test_a_failed_validation_resets_the_worktree_and_escalates_the_ticket_and_the_spec(
    tmp_path: Path, handler_for, reason: str
) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run(handler=handler_for(harness))

    assert code == 1
    assert harness.git.branch_commits["add-login-page"] == []
    assert harness.git.pushed == [] and all(c[:2] != ("pr", "create") for c in harness.gh.calls)
    assert {c[2] for c in harness.gh.calls if c[:2] == ("issue", "edit") and c[-1] == "hitl"} == {"1", "10"}
    comments = [c for c in harness.gh.calls if c[:2] == ("issue", "comment")]
    assert {c[2] for c in comments} == {"1", "10"} and all(reason in c[-1] for c in comments)
    assert all(c[:2] != ("issue", "close") for c in harness.gh.calls)


def test_two_commits_in_one_run_fail_the_run(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt, options):
        harness.git.commit(_only_worktree(harness.git), "ccode(Checkout|10): first")
        return _commit_and_report(harness, 10)

    code = harness.run(handler=handler)

    assert code == 1
    assert harness.git.branch_commits["add-login-page"] == []
    assert any("exactly one commit" in c[-1] for c in harness.gh.calls if c[:2] == ("issue", "comment"))


def test_an_uncommitted_change_fails_the_run(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt, options):
        response = _commit_and_report(harness, 10)
        harness.git.dirty_worktrees.add(_only_worktree(harness.git))
        return response

    code = harness.run(handler=handler)

    assert code == 1
    assert any("uncommitted" in c[-1] for c in harness.gh.calls if c[:2] == ("issue", "comment"))


def test_a_failed_response_after_committing_discards_the_commit_and_comments_its_reason(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt, options):
        harness.git.commit(_only_worktree(harness.git), "ccode(Checkout|10): half")
        return _envelope(status="failed", result={"reason": "needs a decision"})

    code = harness.run(handler=handler)

    assert code == 1
    assert harness.git.branch_commits["add-login-page"] == []
    assert harness.git.pushed == []
    comments = [c for c in harness.gh.calls if c[:2] == ("issue", "comment")]
    assert comments and all("needs a decision" in c[-1] for c in comments)


def test_a_failed_ticket_stops_the_delivery_of_the_next_ticket(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: [_issue(10, "Add login form"), _issue(11, "Add login tests")]})

    code = harness.run(handler=lambda prompt, options: "")

    assert code == 1
    assert len(harness.agent_calls) == 2
    assert all(c[2] != "11" for c in harness.gh.calls if c[:2] in (("issue", "close"), ("issue", "edit")))


def test_only_the_selected_ticket_is_closed(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: [_issue(10, "Add login form"), _issue(11, "Add login tests")]})

    harness.run(handler=lambda prompt, options: _commit_and_report(harness, 10) if "Checkout|10" in prompt else "")

    closes = [c[2] for c in harness.gh.calls if c[:2] == ("issue", "close")]
    assert closes == ["10"]


def test_implementations_follow_the_contracts() -> None:
    assert issubclass(CopilotClient, AgentClient) and issubclass(FakeAgentClient, AgentClient)


def test_core_never_imports_adapters() -> None:
    adapter_packages = ("loop.agents", "loop.runs", "loop.stores", "loop.platforms")
    core = [p for p in SRC.glob("*.py") if p.name != "__init__.py"] + list((SRC / "contracts").glob("*.py"))
    for path in core:
        for module in imports_of(path):
            assert not module.startswith(adapter_packages), path


def test_workflows_import_only_the_public_api() -> None:
    for path in WORKFLOWS.rglob("*.py"):
        for module in imports_of(path):
            assert not module.startswith("loop.") and (
                not module.startswith("workflows") or module.startswith("workflows.platforms")
            ), (path, module)

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from loop import BranchStrategy
from workflow_harness import COMPLETED, DevHarness, crash, envelope, hook_error, issue
from workflows import dev
from workflows.dev import FileExecutionStore
from workflows.platforms.work_tracking import GitHubClient, RepositoryConfig, Spec, TicketsTracker
from workflows.platforms.work_tracking.fake_gh_cli import FakeGhCli

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
TWO_TICKETS = {1: [issue(10, "Add login form"), issue(11, "Add login tests")]}
TICKET_10_URL = "https://github.com/owner/repo/issues/10"


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
    args = dev.prompt_args(_first_ticket(), dev.WorkIdentifier("Checkout", 10), ["abc1234 first"], Path("/w"), "main", "f")

    assert set(_PLACEHOLDER.findall(dev.PROMPT.read_text())) == set(args)


def test_prompt_args_carry_only_the_ticket_its_task_id_and_the_initiative_commits() -> None:
    args = dev.prompt_args(_first_ticket(), dev.WorkIdentifier("Checkout", 10), [], Path("/w"), "main", "feature")

    ticket_json = json.loads(args["TICKET_JSON"])
    assert (ticket_json["number"], ticket_json["body"]) == (10, "Body of #10")
    assert args["TASK_ID"] == "Checkout|10"
    assert "No task commits" in args["INITIATIVE_COMMITS"]
    assert {"SPEC_JSON", "TICKETS_JSON", "RECENT_COMMITS"}.isdisjoint(args)


def test_agents_run_in_the_worktree_on_the_feature_branch_based_on_the_target(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run(lambda prompt: harness.commit_and_report(10))

    assert code == 0
    options = harness.git_service.opened[0]
    assert options.strategy == BranchStrategy("add-login-page", "origin/main")
    assert options.root_path == harness.root / "workspace" / f"{harness.root.name}.worktrees"
    assert f"`{harness.worktree}`" in harness.prompts[0]
    assert harness.git_service.closed == 1


def test_a_log_dir_override_writes_logs_to_that_folder(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, specs=[], tickets={})
    custom = tmp_path / "custom-logs"

    code = harness.run(argv=["--log-dir", str(custom), "--log-level", "DEBUG"])

    assert code == 0 and (custom / "dev.log").is_file()


@pytest.mark.parametrize("harnesses", [0, 2])
def test_exits_before_reading_specs_unless_exactly_one_repository_is_the_harness(tmp_path: Path, harnesses: int) -> None:
    harness = DevHarness(tmp_path)
    repositories = [
        RepositoryConfig(path=tmp_path / str(index), owner_repo=f"owner/r{index}", is_harness=index < harnesses)
        for index in range(2)
    ]

    code = harness.run(repositories=repositories)

    assert code == 1 and harness.gh.calls == []


def test_an_unexpected_error_is_logged_and_the_process_exits_non_zero(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    class RaisingGithub:
        def spec_issues(self):
            raise RuntimeError("boom")

    code = harness.run(
        github_factory=lambda checkout: RaisingGithub(),
        argv=["--log-dir", str(harness.log_dir), "--log-level", "DEBUG"],
    )

    assert code == 1
    assert "boom" in (harness.log_dir / "dev.log").read_text()


def test_the_spec_is_fetched_from_the_harness_when_it_targets_the_harness_origin(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run(lambda prompt: harness.commit_and_report(10))

    assert code == 0 and harness.git.fetched == [harness.root]


def test_a_target_repo_is_fetched_from_its_clone_and_gets_the_pull_request(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[issue(1, "Add a widget", labels=("spec", "repo:target:acme/widgets", "repo:base:main"))],
        tickets={1: [issue(10, "Build the widget")]},
    )
    clone = harness.root / "workspace" / "widgets"
    target_gh = FakeGhCli(specs=[], tickets={})
    target_github = GitHubClient("acme", "widgets", gh=target_gh)

    code = harness.run(
        lambda prompt: harness.commit_and_report(10, initiative="1"),
        github_factory=lambda checkout: harness.github if checkout == harness.root else target_github,
        repositories=[
            RepositoryConfig(path=harness.root, owner_repo="owner/repo", is_harness=True),
            RepositoryConfig(path=clone, owner_repo="acme/widgets"),
        ],
    )

    assert code == 0
    assert harness.git.fetched == [clone]
    assert harness.git_service.opened[0].repository_path == clone
    assert any(call[:2] == ("pr", "create") for call in target_gh.calls)
    assert harness.calls("issue", "close")


def test_an_unconfigured_repo_target_is_labelled_hitl_with_no_worktree_opened(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[issue(1, "Add a widget", labels=("spec", "repo:target:acme/widgets", "repo:base:main"))],
        tickets={1: [issue(10, "Build the widget")]},
    )

    code = harness.run()

    assert code == 0
    assert harness.git_service.opened == [] and harness.git.fetched == []
    labels, comments = harness.calls("issue", "edit"), harness.calls("issue", "comment")
    assert labels[0][-1] == "hitl"
    assert "repo:target:acme/widgets" in comments[0][-1] and "RepositoryPool" in comments[0][-1]


@pytest.mark.parametrize(
    "labels",
    [
        ("spec", "repo:base:main"),
        ("spec", "repo:target:not-a-slug", "repo:base:main"),
        ("spec", "repo:target:owner/repo", "repo:target:other/one", "repo:base:main"),
    ],
)
def test_a_bad_target_label_is_labelled_hitl_and_never_falls_back_to_the_harness(
    tmp_path: Path, labels: tuple[str, ...]
) -> None:
    harness = DevHarness(tmp_path, specs=[issue(1, "Add login page", labels=labels)])

    code = harness.run()

    assert code == 0 and harness.git.fetched == []
    assert harness.calls("issue", "edit")[0][-1] == "hitl"


def test_a_spec_labelled_hitl_is_skipped(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[issue(1, "Add login page", labels=("spec", "hitl", "repo:target:owner/repo", "repo:base:main"))],
    )

    code = harness.run()

    assert code == 0 and harness.writes() == []


def test_a_missing_remote_target_branch_is_labelled_hitl_with_no_worktree_opened(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.remote_branches.discard("main")

    code = harness.run()

    assert code == 0
    assert harness.calls("issue", "edit")[0][-1] == "hitl"
    assert harness.git_service.opened == []


def test_a_ticket_that_failed_once_in_an_earlier_run_is_escalated_on_its_next_failure_without_a_retry(
    tmp_path: Path,
) -> None:
    harness = DevHarness(tmp_path)
    harness.store.record_failure(TICKET_10_URL, owner="owner", repo="repo", task_id="10", title="x", items=[10])

    code = harness.run()

    assert code == 1 and len(harness.prompts) == 1
    assert {c[2] for c in harness.calls("issue", "edit") if c[-1] == "hitl"} == {"1", "10"}


def test_failures_persist_per_ticket_in_the_days_execution_log(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    store = FileExecutionStore(harness.log_dir, clock=lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

    code = harness.run(store=store)

    assert code == 1
    records = json.loads((harness.log_dir / "dev-execution-log-2026-01-01.json").read_text())
    assert (records[0]["count"], records[0]["last_run"], records[0]["last_items"]) == (2, "2026-01-01T12:00:00Z", [10])


def test_a_first_failure_retries_the_same_ticket_immediately_without_hitl(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run(lambda prompt: "" if len(harness.prompts) == 1 else harness.commit_and_report(10))

    assert code == 0 and len(harness.prompts) == 2
    assert harness.calls("issue", "edit") == []
    assert [c[2] for c in harness.calls("issue", "close")] == ["10"]
    assert harness.store.failed_attempts(TICKET_10_URL) == 0


def test_two_tickets_each_failing_once_are_not_escalated(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets=TWO_TICKETS)
    seen: set[int] = set()

    def handler(prompt: str) -> str:
        number = 10 if "Task id: `Checkout|10`" in prompt else 11
        if number not in seen:
            seen.add(number)
            return ""
        return harness.commit_and_report(number)

    code = harness.run(handler)

    assert code == 0 and harness.calls("issue", "edit") == []
    assert [c[2] for c in harness.calls("issue", "close")] == ["10", "11"]


def test_a_failing_worktree_hook_fails_the_spec_with_its_reason(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git_service.open_error = hook_error()

    code = harness.run()

    assert code == 1 and harness.prompts == []
    assert any("setup.sh" in c[-1] for c in harness.calls("issue", "comment"))


def test_a_prompt_placeholder_with_no_argument_fails_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    template = tmp_path / "dev.md"
    template.write_text("Task {{TASK_ID}} {{UNKNOWN}}")
    monkeypatch.setattr(dev, "PROMPT", template)
    harness = DevHarness(tmp_path)

    code = harness.run()

    assert code == 1 and harness.prompts == []
    assert any("missing prompt argument: UNKNOWN" in c[-1] for c in harness.calls("issue", "comment"))


def test_a_complete_ticket_is_pushed_prd_and_closed_with_sha_summary_and_verification(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    reported: list[str] = []

    def handler(prompt: str) -> str:
        response = harness.commit_and_report(10, summary="added the form")
        reported.append(json.loads(response)["result"]["commit"])
        return response

    code = harness.run(handler)

    assert code == 0
    assert harness.git.pushes == [(harness.worktree, "add-login-page")]
    close = harness.calls("issue", "close")[0]
    assert close[2] == "10"
    assert reported[0] in close[-1] and "added the form" in close[-1] and "ran tests" in close[-1]
    assert len(harness.calls("pr", "create")) == 1


def test_two_actionable_tickets_each_get_a_fresh_run_in_order(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets=TWO_TICKETS)
    numbers = iter((10, 11))

    code = harness.run(lambda prompt: harness.commit_and_report(next(numbers)))

    assert code == 0 and len(harness.prompts) == 2
    assert [c[2] for c in harness.calls("issue", "close")] == ["10", "11"]
    assert len(harness.git.pushes) == 1


def test_all_tickets_delivered_comments_the_pull_request_link_on_the_open_spec(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets=TWO_TICKETS)
    numbers = iter((10, 11))

    harness.run(lambda prompt: harness.commit_and_report(next(numbers)))

    spec_comments = [c for c in harness.calls("issue", "comment") if c[2] == "1"]
    assert len(spec_comments) == 1 and "pull" in spec_comments[0][-1]
    assert all(c[2] != "1" for c in harness.calls("issue", "close"))


def test_a_ticket_prompt_excludes_the_spec_body_and_other_tickets(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets=TWO_TICKETS)

    harness.run()

    prompt = harness.prompts[0]
    assert "Body of #10" in prompt and "Checkout|10" in prompt
    assert "Body of #1" not in prompt.replace("Body of #10", "") and "Add login tests" not in prompt


def test_the_prompt_lists_only_this_initiatives_task_commits(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    for subject in ("ccode(Checkout|9): earlier work", "ccode(Other|3): other initiative", "ccode: bare"):
        harness.git.commit(subject)

    harness.run()

    prompt = harness.prompts[0]
    assert "ccode(Checkout|9): earlier work" in prompt
    assert "other initiative" not in prompt and "bare" not in prompt


def test_the_prompt_states_when_the_initiative_has_no_task_commits(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    harness.run()

    assert "No task commits for this Initiative" in harness.prompts[0]


def test_a_hitl_stop_still_pushes_the_commits_of_tickets_closed_in_the_run(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets=TWO_TICKETS)

    code = harness.run(lambda prompt: harness.commit_and_report(10) if "Checkout|10" in prompt else "")

    assert code == 1
    assert len(harness.git.pushes) == 1 and harness.calls("pr", "create")
    assert all("pull request" not in c[-1] for c in harness.calls("issue", "comment") if c[2] == "1")


def test_a_branch_ahead_at_spec_start_is_pushed_from_the_checkout_before_the_worktree_opens(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.commit("ccode(Checkout|9): earlier")
    pushed_before_open: list[bool] = []
    harness.git_service.on_open = lambda: pushed_before_open.append(bool(harness.git.pushes))

    harness.run()

    assert harness.git.pushes[0] == (harness.root, "add-login-page")
    assert pushed_before_open == [True]
    assert harness.calls("pr", "create")


def test_a_spec_with_no_actionable_ticket_and_a_branch_ahead_is_pushed_with_its_pr(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: []})
    harness.git.commit("ccode(Checkout|9): earlier")

    code = harness.run()

    assert code == 0
    assert harness.git.pushes == [(harness.root, "add-login-page")]
    assert harness.calls("pr", "create")


def test_nothing_ahead_means_no_push_and_no_pull_request(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: []})

    code = harness.run()

    assert code == 0 and harness.git.pushes == [] and harness.writes() == []


def test_repeating_a_run_with_the_remote_up_to_date_pushes_nothing_more(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    harness.run(lambda prompt: harness.commit_and_report(10))
    harness.run(lambda prompt: "")

    assert len(harness.git.pushes) == 1


def test_a_draft_pr_already_open_for_the_branch_is_not_duplicated(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        prs=[{"number": 5, "title": "existing", "url": "https://github.com/owner/repo/pull/5", "headRefName": "add-login-page"}],
    )

    code = harness.run(lambda prompt: harness.commit_and_report(10))

    assert code == 0 and harness.calls("pr", "create") == []


def test_agent_output_noise_before_the_response_is_ignored(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt: str) -> str:
        noise = envelope("Other|1", result=COMPLETED)
        return f"working...\n{noise}\nmore text\n{harness.commit_and_report(10)}\n"

    assert harness.run(handler) == 0


@pytest.mark.parametrize(
    ("make_handler", "reason"),
    [
        (lambda h: lambda p: envelope(result=COMPLETED), "HEAD did not change"),
        (lambda h: lambda p: "no json here", "no response object"),
        (lambda h: lambda p: '{"identifier": "Checkout|10", "status": "completed", "result": {"commit"', "no response object"),
        (lambda h: h.commit_then(lambda c: envelope("Checkout|11", result={**COMPLETED, "commit": c})), "identifier"),
        (lambda h: h.commit_then(lambda c: envelope(result={**COMPLETED, "commit": "deadbeef"})), "result.commit"),
        (lambda h: h.commit_then(lambda c: envelope(result={"commit": c, "summary": "s"})), "required"),
        (lambda h: lambda p: h.commit_and_report(10, subject="feat: wrong subject"), "does not start with"),
        (lambda h: crash(), "did not exit successfully"),
    ],
)
def test_a_failed_validation_restores_the_worktree_and_escalates_the_ticket_and_the_spec(
    tmp_path: Path, make_handler, reason: str
) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run(make_handler(harness))

    assert code == 1
    assert len(harness.git.commits) == 1 and harness.git.restores
    assert harness.git.pushes == [] and harness.calls("pr", "create") == []
    assert {c[2] for c in harness.calls("issue", "edit") if c[-1] == "hitl"} == {"1", "10"}
    comments = harness.calls("issue", "comment")
    assert {c[2] for c in comments} == {"1", "10"} and all(reason in c[-1] for c in comments)
    assert harness.calls("issue", "close") == []


def test_two_commits_in_one_run_fail_the_run(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt: str) -> str:
        harness.git.commit("ccode(Checkout|10): first")
        return harness.commit_and_report(10)

    code = harness.run(handler)

    assert code == 1 and len(harness.git.commits) == 1
    assert any("exactly one commit" in c[-1] for c in harness.calls("issue", "comment"))


def test_an_uncommitted_change_fails_the_run(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt: str) -> str:
        response = harness.commit_and_report(10)
        harness.git.dirty = True
        return response

    code = harness.run(handler)

    assert code == 1 and harness.git.dirty is False
    assert any("uncommitted" in c[-1] for c in harness.calls("issue", "comment"))


def test_a_failed_response_after_committing_discards_the_commit_and_comments_its_reason(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt: str) -> str:
        harness.git.commit("ccode(Checkout|10): half")
        return envelope(status="failed", result={"reason": "needs a decision"})

    code = harness.run(handler)

    assert code == 1 and len(harness.git.commits) == 1 and harness.git.pushes == []
    assert all("needs a decision" in c[-1] for c in harness.calls("issue", "comment"))


def test_a_failed_ticket_stops_the_delivery_of_the_next_ticket(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets=TWO_TICKETS)

    code = harness.run()

    assert code == 1 and len(harness.prompts) == 2
    assert all(c[2] != "11" for c in harness.calls("issue", "close") + harness.calls("issue", "edit"))


def test_only_the_selected_ticket_is_closed(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets=TWO_TICKETS)

    harness.run(lambda prompt: harness.commit_and_report(10) if "Checkout|10" in prompt else "")

    assert [c[2] for c in harness.calls("issue", "close")] == ["10"]

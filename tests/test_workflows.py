from __future__ import annotations

import ast
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import commit_file, git
from loop import (
    MAX_FAILED_ATTEMPTS,
    AgentClient,
    AgentResult,
    Sandbox,
    CommandError,
    CopilotClient,
    DockerSandbox,
    FileExecutionStore,
    GitHubClient,
    Hook,
    InMemoryExecutionStore,
    NoSandbox,
)
from loop.testing import FakeAgentClient, FakeCopilotCli, FakeGhCli, FakeGitClient
from workflows import dev

SRC = Path(__file__).parents[1] / "src" / "loop"
WORKFLOWS = Path(__file__).parents[1] / "workflows"
_PLACEHOLDER = re.compile(r"\$\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_github_client_reads_specs_tickets_and_pull_requests() -> None:
    client = GitHubClient("owner", "repo", gh=FakeGhCli())

    specs = client.get_specs()
    tickets = client.get_tickets(specs[0])
    pull_request = client.find_pull_request("feature/login")
    threads = client.review_threads("10")

    assert [(item.number, item.title, item.labels) for item in specs] == [
        (1, "Add login page", ("spec",)),
        (2, "Add logout button", ("spec",)),
    ]
    assert [(ticket.number, ticket.state, ticket.labels) for ticket in tickets] == [
        (10, "open", ()),
        (11, "open", ("hitl",)),
        (12, "open", ("spec",)),
        (13, "closed", ()),
    ]
    assert pull_request is not None and (pull_request.number, pull_request.branch) == (10, "feature/login")
    assert client.find_pull_request("missing-branch") is None
    assert [(thread.id, thread.resolved) for thread in threads] == [("t1", False), ("t2", True)]


def test_github_client_dry_run_skips_the_reply() -> None:
    gh = FakeGhCli()

    GitHubClient("owner", "repo", gh=gh, dry_run=True).reply_to_thread("10", "t1", "x")
    GitHubClient("owner", "repo", gh=gh).reply_to_thread("10", "t1", "x")

    assert len(gh.calls) == 1 and "thread=t1" in gh.calls[0]


def test_github_client_for_repo_reads_the_origin_remote(repo: Path) -> None:
    git(repo, "remote", "add", "origin", "https://example.com/owner/repo.git")
    with pytest.raises(ValueError, match="unsupported remote"):
        GitHubClient.for_repo(repo)

    git(repo, "remote", "set-url", "origin", "git@github.com:owner/repo.git")
    harness, target = GitHubClient.for_repo(repo)
    assert isinstance(harness, GitHubClient) and target is harness


# --- Unit tests: report parser, commit-message builder, branch-name rule, metadata parsing ---------------


def _fenced(payload: dict) -> str:
    return f"intro text\n```json\n{json.dumps(payload)}\n```\n"


def test_parse_report_reads_the_final_fenced_json_block() -> None:
    text = (
        "noise\n```json\n{\"tickets\": []}\n```\nmore noise\n```json\n"
        + json.dumps({"tickets": [{"number": 10, "status": "complete", "summary": "done"}]})
        + "\n```\n"
    )

    report = dev.parse_report(text)

    assert report.tickets == (dev.TicketReport(10, "complete", "done"),)


def test_parse_report_raises_when_no_fenced_block_is_present() -> None:
    with pytest.raises(dev.ReportError, match="no fenced"):
        dev.parse_report("just plain text, no fences")


def test_parse_report_raises_on_malformed_json() -> None:
    with pytest.raises(dev.ReportError, match="not valid JSON"):
        dev.parse_report("```json\n{not valid json\n```")


def test_parse_report_raises_on_a_malformed_ticket_entry() -> None:
    with pytest.raises(dev.ReportError, match="malformed ticket"):
        dev.parse_report(_fenced({"tickets": [{"number": "10", "status": "complete", "summary": "x"}]}))


def test_parse_report_accepts_extra_tickets_beyond_the_actionable_set() -> None:
    report = dev.parse_report(
        _fenced(
            {
                "tickets": [
                    {"number": 10, "status": "complete", "summary": "a"},
                    {"number": 999, "status": "blocked", "summary": "b"},
                ]
            }
        )
    )

    assert [ticket.number for ticket in report.tickets] == [10, 999]


def test_feature_branch_name_prefixes_the_slug_with_an_underscored_version() -> None:
    assert dev.feature_branch_name("release/2.4", "Add Login Page!") == "2_4_add-login-page"


def test_feature_branch_name_is_just_the_slug_without_a_version() -> None:
    assert dev.feature_branch_name("main", "Add Login Page!") == "add-login-page"


def test_parse_initiative_splits_on_the_first_colon() -> None:
    assert dev.parse_initiative("Checkout: Add login page") == ("Checkout", "Add login page")


def test_parse_initiative_returns_none_without_a_colon_prefix() -> None:
    assert dev.parse_initiative("Add login page") == (None, "Add login page")


def test_parse_target_label_reads_the_single_repo_target_value() -> None:
    assert dev.parse_target_label(("spec", "repo:target:owner/name")) == "owner/name"


@pytest.mark.parametrize(
    "labels",
    [
        (),
        ("repo:target:not-a-slug",),
        ("repo:target:owner/name", "repo:target:owner/other"),
        ("repo:target:github.com/owner/name",),
    ],
)
def test_parse_target_label_returns_none_when_missing_malformed_or_duplicated(labels: tuple[str, ...]) -> None:
    assert dev.parse_target_label(labels) is None


def test_parse_base_label_reads_the_single_repo_base_value() -> None:
    assert dev.parse_base_label(("repo:base:main",)) == "main"


@pytest.mark.parametrize("labels", [(), ("repo:base:",), ("repo:base:a", "repo:base:b")])
def test_parse_base_label_returns_none_when_missing_empty_or_duplicated(labels: tuple[str, ...]) -> None:
    assert dev.parse_base_label(labels) is None


def test_prompt_template_placeholders_match_the_supplied_arguments_exactly() -> None:
    spec = GitHubClient("o", "r", gh=FakeGhCli()).get_specs()[0]
    actionable = GitHubClient("o", "r", gh=FakeGhCli()).get_actionable_issues(spec)
    args = dev._prompt_args(spec, actionable, ["abc1234 ccode: first"], Path("/w"), "main", "feature")

    placeholders = set(_PLACEHOLDER.findall(dev.PROMPT.read_text()))

    assert placeholders == set(args.keys())


def test_prompt_args_render_the_spec_and_tickets_as_json_with_bodies_and_comments() -> None:
    github = GitHubClient("o", "r", gh=FakeGhCli())
    spec = github.get_specs()[0]
    actionable = github.get_actionable_issues(spec)

    args = dev._prompt_args(spec, actionable, [], Path("/w"), "main", "feature")

    spec_json = json.loads(args["SPEC_JSON"])
    tickets_json = json.loads(args["TICKETS_JSON"])
    assert (spec_json["number"], spec_json["body"]) == (1, "Body of #1")
    assert [ticket["number"] for ticket in tickets_json] == [10]
    assert tickets_json[0]["comments"] == [
        {"author": "alice", "body": "Comment on #10", "created_at": "2026-01-01T00:00:00Z"}
    ]
    assert args["RECENT_COMMITS"] == "(none)"


# --- Functional tests: drive dev.main with fakes at every process boundary --------------------------------


def _issue(number: int, title: str, *, state: str = "OPEN", labels: tuple[str, ...] = ()) -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/issues/{number}",
        "state": state,
        "body": f"Body of #{number}",
        "labels": {"nodes": [{"name": label} for label in labels]},
        "comments": {"nodes": []},
    }


def _make_repo(path: Path, origin: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    commit_file(path, "README.md", "hello\n", "initial")
    git(path, "remote", "add", "origin", origin)
    return path


def _report(*numbers: int, status: str = "complete", summary: str = "done") -> str:
    return _fenced({"tickets": [{"number": n, "status": status, "summary": summary} for n in numbers]})


def _commit_and_report(harness: "DevHarness", *numbers: int, summary: str = "done") -> str:
    harness.git.commit(_only_worktree(harness.git), "work")
    return _report(*numbers, summary=summary)


class DevHarness:
    """Wires `dev.main` to fakes at every process boundary; the harness is a real repo with a github.com origin."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        specs: list[dict] | None = None,
        tickets: dict[int, list[dict]] | None = None,
        prs: list[dict] | None = None,
    ) -> None:
        self.tmp_path = tmp_path
        self.harness_root = _make_repo(tmp_path / "harness", "git@github.com:owner/repo.git")
        self.log_dir = tmp_path / "logs"
        self.git = FakeGitClient()
        self.git.remote_branches.add("main")
        self.gh = FakeGhCli(
            specs=specs
            if specs is not None
            else [_issue(1, "Checkout: Add login page", labels=("spec", "repo:target:owner/repo", "repo:base:main"))],
            tickets=tickets if tickets is not None else {1: [_issue(10, "Add login form")]},
            prs=prs,
        )
        self.github = GitHubClient("owner", "repo", gh=self.gh)
        self.store = InMemoryExecutionStore()
        self.agent_calls: list[tuple[str, object]] = []

    def run(
        self,
        *,
        handler=None,
        agent_factory=None,
        sandbox_factory=None,
        github_factory=None,
        git=None,
        store=None,
        hooks=(),
        retries=0,
        dry_run=False,
        log_dir: Path | None = None,
        cancel: threading.Event | None = None,
    ) -> int:
        git_client = git if git is not None else self.git

        def tracking_handler(prompt, options):
            self.agent_calls.append((prompt, options))
            return (handler or (lambda prompt, options: ""))(prompt, options)

        def default_agent_factory(executor):
            return FakeAgentClient(tracking_handler)

        return dev.main(
            ["--harness-root", str(self.harness_root), "--log-dir", str(log_dir or self.log_dir)],
            git=git_client,
            github_factory=github_factory or (lambda checkout: self.github),
            agent_factory=agent_factory or (None if dry_run else default_agent_factory),
            sandbox_factory=sandbox_factory
            or (lambda workspace, cancel: NoSandbox(workspace, executor=FakeCopilotCli(), cancel=cancel)),
            store=store if store is not None else self.store,
            hooks=hooks,
            retries=retries,
            dry_run=dry_run,
            cancel=cancel,
        )


def _only_worktree(git_client: FakeGitClient) -> Path:
    return next(iter(git_client.worktrees))


def _writes(gh: FakeGhCli) -> list[tuple[str, ...]]:
    return [call for call in gh.calls if call[0] in ("issue", "pr")]


def test_sandbox_is_created_on_the_harness_root_while_the_prompt_carries_the_worktree(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    sandbox_workspaces: list[Path] = []

    def sandbox_factory(workspace: Path, cancel: threading.Event):
        sandbox_workspaces.append(workspace)
        return NoSandbox(workspace, executor=FakeCopilotCli(), cancel=cancel)

    code = harness.run(
        handler=lambda prompt, options: _commit_and_report(harness, 10), sandbox_factory=sandbox_factory
    )

    assert code == 0
    assert sandbox_workspaces == [harness.harness_root]
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
        git=FakeGitClient(),
        github_factory=lambda checkout: github,
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


@pytest.mark.parametrize("origin", [None, "git@gitlab.com:owner/repo.git"])
def test_exits_before_reading_specs_when_the_harness_origin_is_missing_or_not_on_github(
    tmp_path: Path, origin: str | None
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    git(root, "init", "-b", "main")
    if origin is not None:
        git(root, "remote", "add", "origin", origin)
    gh = FakeGhCli()

    code = dev.main(
        ["--harness-root", str(root), "--log-dir", str(tmp_path / "logs")],
        git=FakeGitClient(),
        github_factory=lambda checkout: GitHubClient("owner", "repo", gh=gh),
        store=InMemoryExecutionStore(),
    )

    assert code == 1
    assert gh.calls == []


def test_exits_non_zero_when_the_harness_root_is_not_a_git_repository(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    root.mkdir()
    gh = FakeGhCli()

    code = dev.main(
        ["--harness-root", str(root), "--log-dir", str(tmp_path / "logs")],
        git=FakeGitClient(),
        github_factory=lambda checkout: GitHubClient("owner", "repo", gh=gh),
        store=InMemoryExecutionStore(),
    )

    assert code == 1
    assert gh.calls == []


def test_an_unexpected_error_is_logged_and_the_process_exits_non_zero(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    class RaisingGithub:
        def get_specs(self):
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

    code = harness.run(handler=lambda prompt, options: _commit_and_report(harness, 10), github_factory=github_factory)

    assert code == 0
    assert harness.git.fetched == [clone]
    assert any(call[:2] == ("pr", "create") for call in target_gh.calls)
    assert any(call[:2] == ("issue", "close") for call in harness.gh.calls)


def test_missing_workspace_clone_is_labelled_hitl_with_no_worktree_created(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[_issue(1, "Add a widget", labels=("spec", "repo:target:acme/widgets", "repo:base:main"))],
        tickets={1: [_issue(10, "Build the widget")]},
    )

    code = harness.run()

    assert code == 0
    assert harness.git.worktrees == {} and harness.git.fetched == []
    labels = [c for c in harness.gh.calls if c[:2] == ("issue", "edit")]
    comments = [c for c in harness.gh.calls if c[:2] == ("issue", "comment")]
    assert labels and labels[0][-1] == "hitl"
    assert comments and "workspace/widgets" in comments[0][-1] and "no clone" in comments[0][-1]


def test_mismatched_origin_clone_is_labelled_hitl_naming_expected_path_and_actual_origin(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[_issue(1, "Add a widget", labels=("spec", "repo:target:acme/widgets", "repo:base:main"))],
        tickets={1: [_issue(10, "Build the widget")]},
    )
    _make_repo(harness.harness_root / "workspace" / "widgets", "git@github.com:someoneelse/widgets.git")

    code = harness.run()

    assert code == 0
    comments = [c for c in harness.gh.calls if c[:2] == ("issue", "comment")]
    assert comments and "someoneelse/widgets" in comments[0][-1]
    assert harness.git.worktrees == {}


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


def test_checkout_failure_in_a_dry_run_is_only_logged_with_no_label_or_comment(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[_issue(1, "Add a widget", labels=("spec", "repo:target:acme/widgets", "repo:base:main"))],
        tickets={1: [_issue(10, "Build the widget")]},
    )
    dry_github = GitHubClient("owner", "repo", gh=harness.gh, dry_run=True)

    code = harness.run(github_factory=lambda checkout: dry_github, dry_run=True)

    assert code == 0
    assert _writes(harness.gh) == []


def test_a_spec_labelled_hitl_is_skipped(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[_issue(1, "Add login page", labels=("spec", "hitl", "repo:target:owner/repo", "repo:base:main"))],
        tickets={1: [_issue(10, "Add login form")]},
    )

    code = harness.run()

    assert code == 0
    assert _writes(harness.gh) == []


def test_no_actionable_tickets_with_earlier_failures_resets_and_skips(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: []})
    harness.store.record_failure(
        "https://github.com/owner/repo/issues/1", owner="owner", repo="repo", task_id="1", title="x", items=[10]
    )

    code = harness.run()

    assert code == 0
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/1") == 0


def test_a_spec_at_the_failed_attempt_cap_is_skipped_without_a_label(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    key = "https://github.com/owner/repo/issues/1"
    for _ in range(MAX_FAILED_ATTEMPTS):
        harness.store.record_failure(key, owner="owner", repo="repo", task_id="1", title="x", items=[10])

    code = harness.run()

    assert code == 0
    assert _writes(harness.gh) == []


def test_a_failed_attempt_records_count_last_run_and_last_items_in_the_days_execution_log(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.failing_fetch.add(harness.harness_root)
    store = FileExecutionStore(harness.log_dir, clock=lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

    code = harness.run(store=store)

    assert code == 1
    path = harness.log_dir / "dev-execution-log-2026-01-01.json"
    records = json.loads(path.read_text())
    assert records[0]["count"] == 1
    assert records[0]["last_run"] == "2026-01-01T12:00:00Z"
    assert records[0]["last_items"] == [10]


def test_a_new_utc_day_lets_a_spec_capped_yesterday_be_attempted_again(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    key = "https://github.com/owner/repo/issues/1"
    yesterday = FileExecutionStore(harness.log_dir, clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    for _ in range(MAX_FAILED_ATTEMPTS):
        yesterday.record_failure(key, owner="owner", repo="repo", task_id="1", title="x", items=[10])
    today = FileExecutionStore(harness.log_dir, clock=lambda: datetime(2026, 1, 2, tzinfo=timezone.utc))

    code = harness.run(store=today)

    assert code == 1
    assert _writes(harness.gh) != []


def test_a_non_array_execution_log_file_fails_before_any_spec_is_attempted(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.log_dir.mkdir(parents=True)
    (harness.log_dir / "dev-execution-log-2026-01-01.json").write_text('{"not": "an array"}')
    store = FileExecutionStore(harness.log_dir, clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))

    code = harness.run(store=store)

    assert code == 1
    assert _writes(harness.gh) == []


def test_when_the_first_spec_fails_in_git_the_second_is_still_processed(tmp_path: Path) -> None:
    harness = DevHarness(
        tmp_path,
        specs=[
            _issue(1, "Add a widget", labels=("spec", "repo:target:acme/widgets", "repo:base:main")),
            _issue(2, "Add logout button", labels=("spec", "repo:target:owner/repo", "repo:base:main")),
        ],
        tickets={1: [_issue(10, "Build the widget")], 2: [_issue(20, "Add logout form")]},
    )
    clone = _make_repo(harness.harness_root / "workspace" / "widgets", "git@github.com:acme/widgets.git")
    harness.git.failing_fetch.add(clone)

    code = harness.run(handler=lambda prompt, options: _commit_and_report(harness, 20))

    assert code == 1
    comments = [c for c in harness.gh.calls if c[:2] == ("issue", "comment") and c[2] == "1"]
    closes = [c for c in harness.gh.calls if c[:2] == ("issue", "close") and c[2] == "20"]
    assert comments and closes


def test_remote_target_branch_missing_is_labelled_hitl(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.remote_branches.discard("main")

    code = harness.run()

    assert code == 0
    edits = [c for c in harness.gh.calls if c[:2] == ("issue", "edit")]
    assert edits and edits[0][-1] == "hitl"
    assert harness.git.worktrees == {}


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
    assert "2_4_add-login-page" in harness.git.branches


def test_a_dirty_leftover_worktree_fails_the_attempt_and_is_left_in_place(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    leftover = Path("/fake/worktrees") / "add-login-page"
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
    assert harness.git.worktrees == {}
    assert len(harness.git.removed) == 1
    assert harness.agent_calls == []


def test_a_cancelled_hook_on_a_clean_worktree_is_reported_cancelled_and_removes_it(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.cancelled_hooks.add("setup.sh")

    code = harness.run(hooks=(Hook("setup.sh"),))

    assert code == 0
    assert harness.git.worktrees == {}
    assert len(harness.git.removed) == 1
    assert _writes(harness.gh) == []
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/1") == 0


def test_a_cancelled_hook_on_a_dirty_worktree_keeps_it_with_no_publication_or_label(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.cancelled_hooks.add("setup.sh")
    harness.git.branches["add-login-page"] = ["already-dirty"]

    code = harness.run(hooks=(Hook("setup.sh"),))

    assert code == 0
    assert harness.git.worktrees
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
    assert harness.git.worktrees == {}
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
    assert harness.git.worktrees
    assert harness.git.removed == []
    assert _writes(harness.gh) == []
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/1") == 0


def test_any_attempt_ending_removes_the_worktree_but_keeps_the_local_branch(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run()

    assert code == 1
    assert harness.git.worktrees == {}
    assert len(harness.git.removed) == 1
    assert "add-login-page" in harness.git.branches


def test_a_prompt_placeholder_with_no_argument_fails_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad_template = tmp_path / "bad.md"
    bad_template.write_text("Spec ${{NOT_A_REAL_KEY}}")
    monkeypatch.setattr(dev, "PROMPT", bad_template)
    harness = DevHarness(tmp_path)

    code = harness.run()

    assert code == 1
    assert harness.agent_calls == []


def test_one_retry_runs_two_fresh_sandboxes_on_the_same_worktree_and_applies_the_second_report(
    tmp_path: Path,
) -> None:
    harness = DevHarness(tmp_path)
    calls: list[int] = []

    def handler(prompt, options):
        calls.append(1)
        if len(calls) == 1:
            return AgentResult("", "boom", 1)
        return _commit_and_report(harness, 10)

    code = harness.run(handler=handler, retries=1)

    assert code == 0
    assert len(harness.agent_calls) == 2
    assert any(c[:2] == ("issue", "close") for c in harness.gh.calls)


def test_exhausted_retries_commit_push_pr_and_label_the_spec_and_tickets_hitl(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt, options):
        harness.git.commit(_only_worktree(harness.git), "work")
        return "no fenced block here"

    code = harness.run(handler=handler, retries=1)

    assert code == 1
    assert len(harness.agent_calls) == 2
    assert harness.git.pushed != []
    assert any(c[:2] == ("pr", "create") for c in harness.gh.calls)
    edits = [c for c in harness.gh.calls if c[:2] == ("issue", "edit") and c[-1] == "hitl"]
    assert {c[2] for c in edits} == {"1", "10"}
    comments = [c for c in harness.gh.calls if c[:2] == ("issue", "comment") and c[2] == "1"]
    assert comments


def test_a_valid_partial_report_is_not_retried(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt, options):
        return _fenced({"tickets": [{"number": 10, "status": "partial", "summary": "working"}]})

    harness.run(handler=handler, retries=2)

    assert len(harness.agent_calls) == 1


def test_dry_run_prepares_the_worktree_runs_hooks_logs_the_prompt_and_writes_nothing(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run(dry_run=True, hooks=(Hook("echo ready"),))

    assert code == 0
    assert harness.git.hook_calls == ["echo ready"]
    assert harness.git.worktrees == {}
    assert [c for c in harness.gh.calls if c[0] != "api"] == []
    log_text = (harness.log_dir / "dev.log").read_text()
    assert '\\"number\\": 1' in log_text and "Add login page" in log_text


def test_every_complete_ticket_with_changes_commits_pushes_prs_and_closes(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run(handler=lambda prompt, options: _commit_and_report(harness, 10, summary="added the form"))

    assert code == 0
    assert harness.git.pushed == [(harness.git.removed[0], "add-login-page")]
    closes = [c for c in harness.gh.calls if c[:2] == ("issue", "close")]
    assert closes and closes[0][2] == "10" and closes[0][-1] == "added the form"
    assert any(c[:2] == ("pr", "create") for c in harness.gh.calls)


def test_python_pushes_the_agents_commit_without_committing_itself(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run(handler=lambda prompt, options: _commit_and_report(harness, 10))

    assert code == 0
    assert list(harness.git.subjects.values()) == ["work"]
    assert len(harness.git.pushed) == 1


def test_the_prompt_carries_earlier_ccode_commits_and_commits_before_the_run_are_not_pushed(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.git.branches["add-login-page"] = ["c1", "c2"]
    harness.git.subjects.update({"c1": "ccode: earlier work", "c2": "unrelated"})

    code = harness.run(handler=lambda prompt, options: _report(10))

    assert code == 0
    assert "ccode: earlier work" in harness.agent_calls[0][0]
    assert "unrelated" not in harness.agent_calls[0][0]
    assert harness.git.pushed == []


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


def test_a_ticket_partial_on_two_consecutive_attempts_is_commented_both_times_and_labelled_hitl_on_the_second(
    tmp_path: Path,
) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt, options):
        return _fenced({"tickets": [{"number": 10, "status": "partial", "summary": "still working"}]})

    code1 = harness.run(handler=handler)
    code2 = harness.run(handler=handler)

    assert code1 == 1 and code2 == 1
    comments = [c for c in harness.gh.calls if c[:2] == ("issue", "comment") and c[2] == "10"]
    assert len(comments) == 2
    edits = [c for c in harness.gh.calls if c[:2] == ("issue", "edit") and c[2] == "10"]
    assert edits and edits[-1][-1] == "hitl"


def test_a_blocked_ticket_is_labelled_hitl(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt, options):
        return _fenced({"tickets": [{"number": 10, "status": "blocked", "summary": "needs a decision"}]})

    code = harness.run(handler=handler)

    assert code == 1
    edits = [c for c in harness.gh.calls if c[:2] == ("issue", "edit") and c[2] == "10"]
    assert edits and edits[0][-1] == "hitl"


def test_a_complete_ticket_with_no_changes_is_closed_with_no_commit(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run(handler=lambda prompt, options: _report(10))

    assert code == 0
    closes = [c for c in harness.gh.calls if c[:2] == ("issue", "close")]
    assert closes and closes[0][2] == "10"
    assert harness.git.pushed == []


def test_no_changes_and_no_complete_ticket_counts_as_a_failed_attempt(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt, options):
        return _fenced({"tickets": [{"number": 10, "status": "blocked", "summary": "needs a decision"}]})

    code = harness.run(handler=handler)

    assert code == 1
    assert harness.git.pushed == []


def test_report_omitting_an_actionable_ticket_leaves_it_untouched_and_ignores_non_actionable_entries(
    tmp_path: Path,
) -> None:
    harness = DevHarness(tmp_path, tickets={1: [_issue(10, "Add login form"), _issue(11, "Add login tests")]})

    def handler(prompt, options):
        harness.git.commit(_only_worktree(harness.git), "work")
        return _fenced(
            {
                "tickets": [
                    {"number": 10, "status": "complete", "summary": "done"},
                    {"number": 999, "status": "complete", "summary": "ignored"},
                ]
            }
        )

    code = harness.run(handler=handler)

    assert code == 0
    touched = {
        c[2] for c in harness.gh.calls if c[:2] in (("issue", "close"), ("issue", "comment"), ("issue", "edit"))
    }
    assert "10" in touched and "11" not in touched and "999" not in touched


def test_a_failure_after_the_agent_started_with_changes_commits_and_pushes_before_removing_the_worktree(
    tmp_path: Path,
) -> None:
    harness = DevHarness(tmp_path)

    def handler(prompt, options):
        harness.git.commit(_only_worktree(harness.git), "work")
        raise CommandError("copilot", None, "crashed mid-run")

    code = harness.run(handler=handler)

    assert code == 1
    assert harness.git.pushed == [(harness.git.removed[0], "add-login-page")]
    assert any(c[:2] == ("pr", "create") for c in harness.gh.calls)
    assert harness.git.worktrees == {}


def test_implementations_follow_the_contracts() -> None:
    assert issubclass(CopilotClient, AgentClient) and issubclass(FakeAgentClient, AgentClient)
    assert issubclass(NoSandbox, Sandbox) and issubclass(DockerSandbox, Sandbox)


def test_core_never_imports_adapters() -> None:
    adapter_packages = ("loop.agents", "loop.sandboxes", "loop.stores", "loop.platforms")
    core = [p for p in SRC.glob("*.py") if p.name != "__init__.py"] + list((SRC / "contracts").glob("*.py"))
    for path in core:
        for module in imports_of(path):
            assert not module.startswith(adapter_packages), path


def test_workflows_import_only_the_public_api() -> None:
    for path in WORKFLOWS.glob("*.py"):
        for module in imports_of(path):
            assert not module.startswith("loop.") and not module.startswith("workflows"), (path, module)

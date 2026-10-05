from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from conftest import commit_file, git
from orb import (
    FileExecutionStore,
    MAX_FAILED_ATTEMPTS,
    ScriptedAgent,
    platform_from_remote,
    worktree,
)
from orb.platforms.azure_devops import AzureDevOpsAdapter
from orb.platforms.fake_az import FakeAz
from orb.platforms.fake_gh import FakeGh
from orb.platforms.github import GitHubAdapter
from workflows import address_prs, dev, fix_prs, parallel_planner

SRC = Path(__file__).parents[1] / "src" / "orb"
WORKFLOWS = Path(__file__).parents[1] / "workflows"
GITHUB_REMOTE = "https://github.com/owner/repo.git"
AZURE_REMOTE = "https://dev.azure.com/org/project/_git/repo"


def imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("remote_url", [GITHUB_REMOTE, AZURE_REMOTE])
def test_platforms_yield_identical_normalized_models(remote_url: str) -> None:
    platform = platform_from_remote(remote_url, gh=FakeGh(), az=FakeAz())

    assert [(s.title, s.state, s.tags[0]) for s in platform.list_specs()] == [
        ("Add login page", "open", "spec"),
        ("Add logout button", "open", "spec"),
    ]
    pull_request = platform.list_pull_requests()[0]
    assert (pull_request.title, pull_request.branch) == ("Add login page (PR)", "feature/login")
    assert [(t.path, t.body, t.resolved) for t in platform.review_threads(pull_request.id)] == [
        ("src/app.py", "Rename this variable.", False),
        ("src/app.py", "Looks good.", True),
    ]


def test_adapter_selection_and_unsupported_remote() -> None:
    assert isinstance(platform_from_remote(GITHUB_REMOTE, gh=FakeGh()), GitHubAdapter)
    assert isinstance(platform_from_remote(AZURE_REMOTE, az=FakeAz()), AzureDevOpsAdapter)
    with pytest.raises(ValueError, match="unsupported remote"):
        platform_from_remote("https://example.com/x/y")


def test_azure_reply_posts_a_json_body_file() -> None:
    az = FakeAz()
    AzureDevOpsAdapter("org", "project", "repo", az=az).reply_to_thread("7", "5", "done")

    call = az.calls[0]
    assert call.count("--route-parameters") == 1 and "threadId=5" in call
    assert "--in-file" in call


def test_dry_run_skips_reply() -> None:
    gh, az = FakeGh(), FakeAz()
    GitHubAdapter("o", "r", gh=gh, dry_run=True).reply_to_thread("10", "t1", "x")
    AzureDevOpsAdapter("o", "p", "r", az=az, dry_run=True).reply_to_thread("7", "5", "x")

    assert gh.calls == [] and az.calls == []


def role_agent(repo_role_handlers: dict[str, object]) -> ScriptedAgent:
    def handle(prompt: str, cwd: Path) -> str:
        role = prompt.split("\n", 1)[0].removeprefix("# ")
        return repo_role_handlers[role](prompt, cwd)  # type: ignore[operator]

    return ScriptedAgent(handle)


def test_parallel_planner_plans_implements_reviews_and_merges(repo: Path) -> None:
    plans = iter([
        {"issues": [{"number": n, "title": f"T{n}", "branch": f"orb/issue-{n}"} for n in (1, 2)]},
        {"issues": []},
    ])
    reviewed: list[str] = []

    def implement(prompt: str, cwd: Path) -> str:
        number = prompt.split("#")[2].split(":")[0]
        commit_file(cwd, f"issue{number}.txt", number, f"ISSUE-{number}")
        return "ok"

    def review(prompt: str, cwd: Path) -> str:
        reviewed.append(prompt)
        return "fine"

    def merge(prompt: str, cwd: Path) -> str:
        for line in prompt.splitlines():
            if line.startswith("- orb/"):
                git(cwd, "merge", "--no-edit", line.removeprefix("- "))
        return "merged"

    agent = role_agent({
        "PLANNER": lambda p, c: f"<plan>{json.dumps(next(plans))}</plan>",
        "IMPLEMENTER": implement,
        "REVIEWER": review,
        "MERGER": merge,
    })

    code = parallel_planner.main(
        ["--repo", str(repo), "--list-issues-command", "echo []", "--max-iterations", "3"],
        agent=agent,
    )

    assert code == 0
    assert (repo / "issue1.txt").exists() and (repo / "issue2.txt").exists()
    assert len(reviewed) == 2 and all("diff --git" in prompt for prompt in reviewed)


def test_parallel_planner_reports_a_failed_issue_and_still_merges_others(repo: Path) -> None:
    plans = iter([
        {"issues": [{"number": n, "title": f"T{n}", "branch": f"orb/issue-{n}"} for n in (1, 2)]},
        {"issues": []},
    ])

    def implement(prompt: str, cwd: Path) -> object:
        from orb import AgentResult

        if "#1:" in prompt:
            return AgentResult(success=False, output="broken")
        commit_file(cwd, "issue2.txt", "2", "ISSUE-2")
        return "ok"

    def merge(prompt: str, cwd: Path) -> str:
        git(cwd, "merge", "--no-edit", "orb/issue-2")
        return "merged"

    agent = role_agent({
        "PLANNER": lambda p, c: f"<plan>{json.dumps(next(plans))}</plan>",
        "IMPLEMENTER": implement,
        "REVIEWER": lambda p, c: "fine",
        "MERGER": merge,
    })

    code = parallel_planner.main(
        ["--repo", str(repo), "--list-issues-command", "echo []"], agent=agent
    )

    assert code == 1
    assert (repo / "issue2.txt").exists() and not (repo / "issue1.txt").exists()


def test_dev_workflow_commits_on_spec_branch_and_caps_attempts(repo: Path, tmp_path: Path) -> None:
    platform = platform_from_remote(GITHUB_REMOTE, gh=FakeGh())
    store = FileExecutionStore(tmp_path / "logs")
    agent = ScriptedAgent(lambda p, cwd: "did nothing")
    argv = ["--repo", str(repo), "--limit", "1"]

    codes = [
        dev.main(argv, agent=agent, platform=platform, store=store) for _ in range(MAX_FAILED_ATTEMPTS + 1)
    ]

    assert codes == [1] * MAX_FAILED_ATTEMPTS + [0]
    assert len(agent.prompts) == MAX_FAILED_ATTEMPTS

    committing = ScriptedAgent(lambda p, cwd: commit_file(cwd, "login.txt", "x", "login") or "ok")
    assert dev.main(argv, agent=committing, platform=platform, store=FileExecutionStore(tmp_path / "other")) == 0
    assert git(repo, "rev-list", "--count", "main..orb/spec-1") == "1"


def test_fix_prs_workflow_commits_on_pull_request_branch(repo: Path, tmp_path: Path) -> None:
    git(repo, "branch", "feature/login")
    platform = platform_from_remote(GITHUB_REMOTE, gh=FakeGh())
    agent = ScriptedAgent(lambda p, cwd: commit_file(cwd, "fix.txt", "x", "fix") or "ok")

    code = fix_prs.main(
        ["--repo", str(repo)], agent=agent, platform=platform, store=FileExecutionStore(tmp_path)
    )

    assert code == 0
    assert "Rename this variable." in agent.prompts[0] and len(agent.prompts) == 1
    assert git(repo, "rev-list", "--count", "main..feature/login") == "1"


def test_address_prs_workflow_posts_the_agents_reply(repo: Path, tmp_path: Path) -> None:
    gh = FakeGh()
    platform = platform_from_remote(GITHUB_REMOTE, gh=gh)
    agent = ScriptedAgent(lambda p, cwd: "Renamed, thanks.\n")

    code = address_prs.main(
        ["--repo", str(repo)], agent=agent, platform=platform, store=FileExecutionStore(tmp_path)
    )

    reply = gh.calls[-1]
    assert code == 0 and "thread=t1" in reply and "body=Renamed, thanks." in reply
    assert git(repo, "branch", "--list") == "* main"


def test_core_never_imports_adapters() -> None:
    adapter_packages = ("orb.agents", "orb.sandboxes", "orb.stores", "orb.platforms")
    core = [p for p in SRC.glob("*.py") if p.name != "__init__.py"] + list((SRC / "contracts").glob("*.py"))
    for path in core:
        for module in imports_of(path):
            assert not module.startswith(adapter_packages), path


def test_workflows_import_only_the_public_api() -> None:
    for path in WORKFLOWS.glob("*.py"):
        for module in imports_of(path):
            assert not module.startswith("orb.") and not module.startswith("workflows"), (path, module)


def test_worktree_provider_is_a_sandbox_provider() -> None:
    from orb import SandboxProvider

    assert isinstance(worktree(), SandboxProvider)

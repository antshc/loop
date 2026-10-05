from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import git
from orb import (
    MAX_FAILED_ATTEMPTS,
    AgentClient,
    AgentClientBase,
    AgentResult,
    Capsule,
    CopilotClient,
    DockerCapsule,
    FakeAgentClient,
    FakeCopilotCli,
    FakeGh,
    FakeGitClient,
    FileExecutionStore,
    GitHubClient,
    InMemorySessionStore,
    NoCapsule,
    copilot,
)
from workflows import dev

SRC = Path(__file__).parents[1] / "src" / "orb"
WORKFLOWS = Path(__file__).parents[1] / "workflows"


def imports_of(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_github_client_reads_specs_issues_and_pull_requests() -> None:
    client = GitHubClient("owner", "repo", gh=FakeGh())

    specs = client.get_specs()
    issues = client.get_issues()
    pull_requests = client.get_pull_requests()
    threads = client.review_threads("10")

    assert [(item.id, item.title, item.tags) for item in specs] == [
        ("1", "Add login page", ("spec",)),
        ("2", "Add logout button", ("spec",)),
    ]
    assert [item.id for item in issues] == ["1", "3"]
    assert [(pr.id, pr.branch) for pr in pull_requests] == [("10", "feature/login")]
    assert [(thread.id, thread.resolved) for thread in threads] == [("t1", False), ("t2", True)]


def test_github_client_dry_run_skips_the_reply() -> None:
    gh = FakeGh()

    GitHubClient("owner", "repo", gh=gh, dry_run=True).reply_to_thread("10", "t1", "x")
    GitHubClient("owner", "repo", gh=gh).reply_to_thread("10", "t1", "x")

    assert len(gh.calls) == 1 and "thread=t1" in gh.calls[0]


def test_github_client_for_repo_reads_the_origin_remote(repo: Path) -> None:
    git(repo, "remote", "add", "origin", "https://example.com/owner/repo.git")
    with pytest.raises(ValueError, match="unsupported remote"):
        GitHubClient.for_repo(repo)

    git(repo, "remote", "set-url", "origin", "git@github.com:owner/repo.git")
    assert isinstance(GitHubClient.for_repo(repo), GitHubClient)


class DevHarness:
    """Dev wired to fakes at every boundary; the fake agent commits into the only open worktree."""

    def __init__(self, tmp_path: Path, outputs: list[str] | None = None) -> None:
        self.tmp_path = tmp_path
        self.git = FakeGitClient()
        self.outputs = iter(outputs or [])
        self.agent = FakeAgentClient(self._handle)
        self.commits = True

    def _handle(self, prompt: str, prompt_args: object, options: object) -> str | AgentResult:
        if self.commits:
            self.git.commit(next(iter(self.git.worktrees)), "work")
        return next(self.outputs, "")

    def run(self, *extra: str, agent_factory=None, executor=None) -> int:
        return dev.main(
            ["--repo", str(self.tmp_path), "--log-dir", str(self.tmp_path / "logs"), *extra],
            agent_factory=agent_factory or (lambda executor: self.agent),
            capsule_factory=lambda workspace, factory: NoCapsule(
                workspace, factory, executor=executor or FakeCopilotCli()
            ),
            git=self.git,
            github=GitHubClient("owner", "repo", gh=FakeGh()),
            store=FileExecutionStore(self.tmp_path / "logs"),
        )


def test_dev_commits_on_the_spec_branch_with_its_session_and_cleans_up(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    code = harness.run()

    prompt, prompt_args, options = harness.agent.calls[0]
    assert code == 0
    assert "${{SPEC_TITLE}}" in prompt
    assert prompt_args == {
        "SOURCE_BRANCH": "orb/spec-1",
        "SPEC_ID": "1",
        "SPEC_TITLE": "Add login page",
        "SPEC_URL": "https://github.com/owner/repo/issues/1",
    }
    assert (options.session_key, options.session_name_prefix) == ("dev-1", "orb-")
    assert len(harness.git.branches["orb/spec-1"]) == 1
    assert harness.git.worktrees == {} and len(harness.git.removed) == 1


def test_dev_caps_attempts_for_a_spec_that_produces_no_commits(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    harness.commits = False

    codes = [harness.run() for _ in range(MAX_FAILED_ATTEMPTS + 1)]

    assert codes == [1, 1, 1, 0]
    assert len(harness.agent.calls) == MAX_FAILED_ATTEMPTS


def test_dev_stops_iterating_at_the_completion_signal(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, ["working", "<promise>COMPLETE</promise>", "never"])

    code = harness.run("--max-iterations", "5")

    assert code == 0 and len(harness.agent.calls) == 2


def test_dev_reports_a_failed_agent_and_still_removes_the_worktree(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, [])
    harness.agent = FakeAgentClient(lambda p, a, o: AgentResult("", "nope", 1))

    code = harness.run()

    assert code == 1 and len(harness.git.removed) == 1


def test_dev_resumes_the_same_agent_session_across_iterations(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    cli = FakeCopilotCli(lambda prompt: harness.git.commit(next(iter(harness.git.worktrees)), "w") and "ok")
    sessions = InMemorySessionStore()

    code = harness.run("--max-iterations", "2", agent_factory=copilot(sessions), executor=cli)

    first, second = cli.calls
    assert code == 0
    assert first[first.index("--name") + 1] == "orb-dev-1" and "--resume=orb-dev-1" in second
    assert "Spec #1: Add login page" in first[2] and "orb/spec-1" in first[2]
    assert sessions.get("dev-1") is not None


def test_implementations_follow_the_contracts() -> None:
    assert issubclass(CopilotClient, AgentClientBase) and issubclass(AgentClientBase, AgentClient)
    assert issubclass(NoCapsule, Capsule) and issubclass(DockerCapsule, Capsule)


def test_core_never_imports_adapters() -> None:
    adapter_packages = ("orb.agents", "orb.capsules", "orb.stores", "orb.platforms")
    core = [p for p in SRC.glob("*.py") if p.name != "__init__.py"] + list((SRC / "contracts").glob("*.py"))
    for path in core:
        for module in imports_of(path):
            assert not module.startswith(adapter_packages), path


def test_workflows_import_only_the_public_api() -> None:
    for path in WORKFLOWS.glob("*.py"):
        for module in imports_of(path):
            assert not module.startswith("orb.") and not module.startswith("workflows"), (path, module)

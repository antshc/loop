from __future__ import annotations

from pathlib import Path

import pytest

from loop import AgentProfile, AgentRequest, AgentWorkspace, copilot
from workflows.tests.dev.doubles import FakeGitService, FakeRepositoryGh, FakeRepositoryGit, FakeRunner, FakeWorkflowGit, new_agent_for
from workflows import dev
from workflows.dev.agents import prompts as prompt_files
from workflows.dev.model.repositories import GithubFactory, Harness, Targets
from workflows.shared.agents.launcher import AgentFactory
from workflows.shared.git import GitClient
from workflows.shared.github.repo import GitHubRepo
from workflows.shared.providers import bind
from workflows.dev.model.records import Commit
from workflows.dev.model.records import Repository, RepositoryId
from workflows.tests.shared.fakes import FakeGhCli, issue
from workflows.dev.model.records import TicketContext, TicketResult


@pytest.mark.parametrize("first_fails", [False, True])
def test_harness_specs_route_to_separate_repositories_and_agent_delegates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_fails: bool
) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "first.md").write_text("Implement {{TITLE}} for the first repository")
    (prompts / "second.md").write_text("Implement {{TITLE}} for the second repository")
    (prompts / "review.md").write_text("Review {{COMMIT}}")
    monkeypatch.setattr(prompt_files, "PROMPTS", prompts)
    harness = Repository(tmp_path, tmp_path / "workspace/harness.worktrees", RepositoryId("owner", "harness"))
    one = Repository(tmp_path / "workspace/one", tmp_path / "workspace/one.worktrees", RepositoryId("owner", "one"))
    two = Repository(tmp_path / "workspace/two", tmp_path / "workspace/two.worktrees", RepositoryId("owner", "two"))
    one_path, two_path = one.worktrees / "first", two.worktrees / "2_4_second"
    one_git, two_git = FakeWorkflowGit(), FakeWorkflowGit()
    one_git.commit("ccode(Checkout|9): one history")
    two_git.commit("ccode(Checkout|19): two history")
    one_git.pushed_count = two_git.pushed_count = 2
    two_git.remote_branches = {"release/2.4"}
    git = FakeRepositoryGit({one.checkout: one_git, one_path: one_git, two.checkout: two_git, two_path: two_git})
    tracker_gh = FakeGhCli(
        specs=[
            issue(1, "Checkout: First", labels=("spec", "repo:target:OWNER/ONE", "repo:base:main")),
            issue(2, "Checkout: Second", labels=("spec", "repo:target:owner/two", "repo:base:release/2.4")),
        ],
        tickets={1: [issue(10, "First task")], 2: [issue(20, "Second task")]},
    )
    one_gh, two_gh = FakeRepositoryGh(one.owner_name), FakeRepositoryGh(two.owner_name)
    githubs = {harness.checkout: tracker_gh, one.checkout: one_gh, two.checkout: two_gh}
    github_paths: list[Path] = []

    def github_for(repository: Repository) -> GitHubRepo:
        github_paths.append(repository.checkout)
        return GitHubRepo(repository.owner_name.owner, repository.owner_name.repo, gh=githubs[repository.checkout])

    implementer = AgentProfile(copilot, "implementation-model")
    reviewer = AgentProfile(copilot, "review-model")
    one_runner, two_runner = FakeRunner(), FakeRunner()
    one_service, two_service = FakeGitService(one_path), FakeGitService(two_path)
    builders = iter((
        new_agent_for(one_runner, one_service, harness.checkout)(),
        new_agent_for(two_runner, two_service, harness.checkout)(),
    ))
    delegates: list[str] = []

    def implement_only(context: TicketContext, worktree: AgentWorkspace) -> TicketResult:
        delegates.append("one")
        assert context.ticket.number == 10
        assert context.initiative_commits == ("c000000 ccode(Checkout|9): one history",)
        assert (context.base_branch, context.feature_branch) == ("main", "first")
        prompt = dev.render_prompt(dev.load_prompt("first"), {"TITLE": context.ticket.title})
        worktree.agent(implementer).run(AgentRequest(prompt))
        sha = one_git.commit(f"{context.identifier.to_subject()}first")
        if first_fails:
            return TicketResult(str(context.identifier), "failed", reason="cannot deliver first")
        return TicketResult(str(context.identifier), "completed", sha, "first delivered", "tested")

    def implement_review(context: TicketContext, worktree: AgentWorkspace) -> TicketResult:
        delegates.append("two")
        assert context.ticket.number == 20
        assert context.initiative_commits == ("c000000 ccode(Checkout|19): two history",)
        assert (context.base_branch, context.feature_branch) == ("release/2.4", "2_4_second")
        prompt = dev.render_prompt(dev.load_prompt("second"), {"TITLE": context.ticket.title})
        worktree.agent(implementer).run(AgentRequest(prompt))
        sha = two_git.commit(f"{context.identifier.to_subject()}second")
        review = dev.render_prompt(dev.load_prompt("review"), {"COMMIT": sha})
        worktree.agent(reviewer).run(AgentRequest(review))
        amended = Commit("reviewed-sha", f"{context.identifier.to_subject()}reviewed")
        two_git.commits[-1] = amended
        return TicketResult(str(context.identifier), "completed", amended.sha, "second reviewed", "tested and reviewed")

    code = dev.main(
        ["--log-dir", str(tmp_path / "logs")],
        providers=(bind(
            (Harness, Harness(harness)), (GithubFactory, github_for), (GitClient, git),
            (AgentFactory, lambda: next(builders)),
        ), dev.use_repositories(dev.RepositoryWorkflow(one, implement_only), dev.RepositoryWorkflow(two, implement_review))),
    )

    assert code == int(first_fails)
    assert delegates == (["one", "one", "two"] if first_fails else ["one", "two"])
    expected_github_paths = [harness.checkout, two.checkout] if first_fails else [harness.checkout, one.checkout, two.checkout]
    assert github_paths == expected_github_paths
    assert one_git.fetched == [one.checkout] and two_git.fetched == [two.checkout]
    assert one_service.opened[0].repository_path == one.checkout
    assert two_service.opened[0].repository_path == two.checkout
    assert one_service.opened[0].root_path == one.worktrees
    assert two_service.opened[0].root_path == two.worktrees
    assert one_service.closed == two_service.closed == 1
    assert one_runner.profiles == [implementer] * (2 if first_fails else 1)
    assert two_runner.profiles == [implementer, reviewer]
    assert one_runner.prompts == ["Implement First task for the first repository"] * (2 if first_fails else 1)
    assert two_runner.prompts == ["Implement Second task for the second repository", "Review c0000002"]
    assert one_git.pushes == ([] if first_fails else [(one_path, "first")])
    assert two_git.pushes == [(two_path, "2_4_second")]
    assert len(two_git.commits) == 3
    for repository, github in ((one, one_gh), (two, two_gh)):
        assert all(call[0] == "pr" and call[call.index("--repo") + 1] == str(repository.owner_name) for call in github.calls)
    writes = [call for call in tracker_gh.calls if call[0] == "issue"]
    assert all(call[call.index("--repo") + 1] == "owner/harness" for call in writes)
    assert not any(call[0] == "pr" for call in tracker_gh.calls)
    assert [call[2] for call in writes if call[1] == "close"] == (["20"] if first_fails else ["10", "20"])
    announcements = [body for _, body in tracker_gh.posted_comments()]
    assert any("https://github.com/owner/two/pull/99" in body for body in announcements)
    if not first_fails:
        assert any("https://github.com/owner/one/pull/99" in body for body in announcements)


def test_duplicate_case_insensitive_repository_configuration_fails_before_processing_specs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = Repository(tmp_path, tmp_path / "workspace", RepositoryId("owner", "repo"))
    duplicate = Repository(tmp_path, tmp_path / "workspace", RepositoryId("OWNER", "REPO"))
    github = FakeRepositoryGh(harness.owner_name)

    def delegate(_context: TicketContext, _worktree: AgentWorkspace) -> TicketResult:
        pytest.fail("duplicate configuration must not invoke a delegate")

    targets = Targets((dev.RepositoryWorkflow(harness, delegate), dev.RepositoryWorkflow(duplicate, delegate)))
    code = dev.main(
        ["--log-dir", str(tmp_path / "logs"), "--log-level", "DEBUG"],
        providers=(bind(
            (Harness, Harness(harness)), (Targets, targets),
            (GithubFactory, lambda repository: GitHubRepo("owner", "repo", gh=github)),
        ),),
    )

    assert code == 1
    assert github.calls == []
    assert "duplicate repository workflow" in (tmp_path / "logs/dev.log").read_text()

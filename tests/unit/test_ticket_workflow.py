from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from loop import AgentWorkspace, GitOptions
from workflow_harness import DevHarness, issue, new_agent_for
from workflows.platforms.work_tracking import RepositoryId, Spec, TicketsTracker
from workflows.tickets import AgentWorkflow, TicketContext, TicketResult, TicketWorkflowError, deliver_tickets


def _deliver(harness: DevHarness, delegate: AgentWorkflow, *, spec: Spec | None = None, max_failures: int = 2) -> bool:
    tracker = TicketsTracker("owner", "repo", gh=harness.gh)
    spec = spec or next(iter(tracker.specs()))
    builder = new_agent_for(harness.runner, harness.git_service, harness.root)()
    with builder.with_git(GitOptions()).open() as worktree:
        return deliver_tickets(
            spec,
            worktree,
            delegate,
            git=harness.git,  # type: ignore[arg-type]
            tracker=tracker,
            store=harness.store,
            ticket_repository=RepositoryId("owner", "repo"),
            max_failures=max_failures,
        )


def _complete(harness: DevHarness, context: TicketContext) -> TicketResult:
    sha = harness.git.commit(f"{context.identifier.to_subject()}work")
    return TicketResult(str(context.identifier), "completed", sha, "done", "ran tests")


def test_delegate_receives_selected_ticket_and_refreshed_initiative_history(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: [issue(10, "First"), issue(11, "Second")]})
    harness.git.commit("ccode(Checkout|9): earlier")
    harness.git.commit("ccode(Other|9): unrelated")
    contexts: list[TicketContext] = []

    def delegate(context: TicketContext, worktree: AgentWorkspace) -> TicketResult:
        contexts.append(context)
        assert worktree.path == harness.worktree
        assert (context.base_branch, context.feature_branch) == ("main", "add-login-page")
        return _complete(harness, context)

    assert _deliver(harness, delegate)
    assert [context.ticket.number for context in contexts] == [10, 11]
    assert contexts[0].initiative_commits == ("c000000 ccode(Checkout|9): earlier",)
    assert contexts[1].initiative_commits == (
        "c000000 ccode(Checkout|9): earlier",
        "c000000 ccode(Checkout|10): work",
    )
    assert [call[2] for call in harness.calls("issue", "close")] == ["10", "11"]
    assert harness.git.pushes == []


def test_retry_excludes_rejected_commits_and_preserves_earlier_work(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)
    earlier = harness.git.commit("ccode(Checkout|9): earlier")
    contexts: list[TicketContext] = []

    def delegate(context: TicketContext, _worktree: AgentWorkspace) -> TicketResult:
        contexts.append(context)
        if len(contexts) == 1:
            harness.git.commit(f"{context.identifier.to_subject()}rejected")
            raise TicketWorkflowError("review found a defect")
        return _complete(harness, context)

    assert _deliver(harness, delegate)
    assert contexts[0].initiative_commits == contexts[1].initiative_commits
    assert harness.git.restores == [earlier]
    assert harness.store.failed_attempts("https://github.com/owner/repo/issues/10") == 0
    assert len(harness.git.commits) == 3


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"identifier": "Checkout|11"}, "does not match task"),
        ({"status": "unknown"}, "unknown result status"),
        ({"summary": None}, "required"),
        ({"verification": None}, "required"),
        ({"commit": None}, "required"),
        ({"status": "failed", "reason": None}, "missing reason"),
        ({"status": "failed", "reason": "review rejected"}, "review rejected"),
    ],
)
def test_delegate_results_are_validated_without_relying_on_a_response_parser(
    tmp_path: Path, changes: dict[str, str | None], reason: str
) -> None:
    harness = DevHarness(tmp_path)
    attempts = 0

    def delegate(context: TicketContext, _worktree: AgentWorkspace) -> TicketResult:
        nonlocal attempts
        attempts += 1
        return replace(_complete(harness, context), **changes)

    assert not _deliver(harness, delegate)
    assert attempts == 2
    assert len(harness.git.commits) == 1
    assert len(harness.git.restores) == 2
    assert all(reason in call[-1] for call in harness.calls("issue", "comment"))
    assert harness.calls("issue", "close") == []


def test_ticket_workflow_skips_non_actionable_tickets_it_is_given(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path, tickets={1: [issue(10, "Closed"), issue(11, "Blocked"), issue(12, "Ready")]})
    spec = next(iter(TicketsTracker("owner", "repo", gh=harness.gh).specs()))
    spec.tickets[0].state = "closed"
    spec.tickets[1].labels = ("hitl",)
    attempted: list[int] = []

    def delegate(context: TicketContext, _worktree: AgentWorkspace) -> TicketResult:
        attempted.append(context.ticket.number)
        return _complete(harness, context)

    assert _deliver(harness, delegate, spec=spec)
    assert attempted == [12]


def test_failure_cap_must_be_positive(tmp_path: Path) -> None:
    harness = DevHarness(tmp_path)

    def delegate(_context: TicketContext, _worktree: AgentWorkspace) -> TicketResult:
        pytest.fail("invalid failure cap must not run agents")

    with pytest.raises(TicketWorkflowError, match="at least one"):
        _deliver(harness, delegate, max_failures=0)

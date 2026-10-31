"""Each role decodes its raw agent output into its typed reply and encodes its request byte for byte."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from loop import AgentReplyError, Declined

from workflows.software_factory.agents.roles import (
    address,
    chore,
    developer,
    maintainer,
    planner,
    reviewer,
)

from .support import ScriptedRunner, envelope, run_agent

WORKTREE = Path("/work/tree")


def _ran(agent, request, output: str):
    return run_agent(agent, request, ScriptedRunner(output))


# developer


def test_the_developer_prompt_carries_the_ticket_the_commits_and_the_branches() -> None:
    """Given a developer request, when it is encoded, then every template placeholder holds its value."""
    prompt = developer.DEVELOPER.encode(
        developer.DeveloperRequest(
            "Checkout|10",
            {"number": 10, "title": "Add form"},
            (),
            "ccode(Checkout|10): ",
            WORKTREE,
            "main",
            "feature",
        )
    )

    assert json.dumps({"number": 10, "title": "Add form"}, indent=2) in prompt
    assert (
        "No task commits for this Initiative exist on the feature branch yet." in prompt
    )
    assert (
        "ccode(Checkout|10): " in prompt
        and "/work/tree" in prompt
        and "{{" not in prompt
    )


def test_the_developer_prompt_lists_the_initiative_commits_oldest_first() -> None:
    """Given initiative commits, when the request is encoded, then they appear one per line."""
    prompt = developer.DEVELOPER.encode(
        developer.DeveloperRequest(
            "Checkout|10",
            {},
            ("abc first", "def second"),
            "ccode(Checkout|10): ",
            WORKTREE,
            "main",
            "feature",
        )
    )

    assert "abc first\ndef second" in prompt and "No task commits" not in prompt


# planner


def test_the_planner_decodes_tickets_in_order_with_their_blockers() -> None:
    """Given two tickets where the second is blocked by the first, when decoded, then both keep order and blockers."""
    output = envelope(
        tickets=[
            {"title": " A ", "body": "a"},
            {"title": "B", "body": "b", "blocked_by": [0]},
        ]
    )

    reply = _ran(
        planner.PLANNER, planner.PlannerRequest(WORKTREE, {}, "main", "feature"), output
    )

    assert reply == planner.Proposed(
        (planner.ProposedTicket("A", "a"), planner.ProposedTicket("B", "b", (0,)))
    )


def test_the_planner_declines_with_the_reason() -> None:
    """Given a failed envelope, when decoded, then the reply is Declined."""
    reply = _ran(
        planner.PLANNER,
        planner.PlannerRequest(WORKTREE, {}, "main", "feature"),
        envelope("failed", reason="ambiguous"),
    )

    assert reply == Declined("ambiguous")


@pytest.mark.parametrize(
    "output",
    [
        "no json",
        envelope(tickets="nope"),
        envelope(tickets=["nope"]),
        envelope(tickets=[{"title": "A", "blocked_by": ["x"]}]),
        envelope(tickets=[{"title": "A", "blocked_by": [True]}]),
        envelope(tickets=[{"title": " "}]),
        envelope("failed"),
        json.dumps({"status": "weird", "result": {}}),
    ],
)
def test_the_planner_rejects_malformed_output_with_a_reply_error(output: str) -> None:
    """Given malformed planner output, when decoded, then a reply error names the planner."""
    with pytest.raises(AgentReplyError, match="^planner agent: "):
        _ran(
            planner.PLANNER,
            planner.PlannerRequest(WORKTREE, {}, "main", "feature"),
            output,
        )


# chore

CHORE_REQUEST = chore.ChoreRequest(
    "https://github.com/o/r/pull/1", WORKTREE, "main", "feature", Path("/work/diff")
)


def test_the_chore_agent_decodes_its_commit_message_summary_and_findings() -> None:
    """Given a completed chore envelope, when decoded, then every field is carried."""
    output = envelope(commit_message="tidy", summary="s", out_of_scope=["old bug", 7])

    assert _ran(chore.CHORE, CHORE_REQUEST, output) == chore.Refined(
        "tidy", "s", ("old bug", "7")
    )


def test_the_chore_agent_declines_with_the_reason() -> None:
    """Given a failed chore envelope, when decoded, then the reply is Declined."""
    assert _ran(
        chore.CHORE, CHORE_REQUEST, envelope("failed", reason="nothing to do")
    ) == Declined("nothing to do")


def test_the_chore_prompt_names_the_empty_branch_when_there_are_no_commits() -> None:
    """Given no branch commits, when encoded, then the prompt says the branch has none."""
    assert "No commits exist on the feature branch yet." in chore.CHORE.encode(
        CHORE_REQUEST
    )


@pytest.mark.parametrize(
    "output", ["no json", envelope(commit_message=3), envelope(out_of_scope="x")]
)
def test_the_chore_agent_rejects_malformed_output_with_a_reply_error(
    output: str,
) -> None:
    """Given malformed chore output, when decoded, then a reply error names the chore agent."""
    with pytest.raises(AgentReplyError, match="^chore agent: "):
        _ran(chore.CHORE, CHORE_REQUEST, output)


# reviewer

REVIEWER_REQUEST = reviewer.ReviewerRequest(
    "https://github.com/o/r/pull/1", WORKTREE, "Checkout", "abc"
)


def test_the_reviewer_decodes_axes_findings_posted_comments_and_the_reviewed_head() -> (
    None
):
    """Given a completed review, when decoded, then outcomes and kinds are lower-cased and references are text."""
    output = envelope(
        axes=[
            {"name": "bugs", "outcome": "completed"},
            {"name": "smells", "outcome": "FAILED", "note": "n"},
        ],
        findings=[{"axis": "bugs", "kind": "Bug", "summary": "npe"}],
        posted=["c1", 7],
        head="abc",
    )

    assert _ran(reviewer.REVIEWER, REVIEWER_REQUEST, output) == reviewer.Reviewed(
        (
            reviewer.ReviewedAxis("bugs", "completed"),
            reviewer.ReviewedAxis("smells", "failed", "n"),
        ),
        (reviewer.ReviewedFinding("bug", "npe", "bugs"),),
        ("c1", "7"),
        "abc",
    )


def test_the_reviewer_declines_with_the_reason() -> None:
    """Given a failed review, when decoded, then the reply is Declined."""
    assert _ran(
        reviewer.REVIEWER, REVIEWER_REQUEST, envelope("failed", reason="no skill")
    ) == Declined("no skill")


@pytest.mark.parametrize(
    "output",
    [
        "no json",
        envelope(axes=["x"]),
        envelope(axes=[{"name": "bugs"}]),
        envelope(findings=[{"kind": "bug"}]),
        envelope(posted="c1"),
    ],
)
def test_the_reviewer_rejects_malformed_output_with_a_reply_error(output: str) -> None:
    """Given malformed reviewer output, when decoded, then a reply error names the reviewer."""
    with pytest.raises(AgentReplyError, match="^reviewer agent: "):
        _ran(reviewer.REVIEWER, REVIEWER_REQUEST, output)


# address


def _json(**fields: object) -> str:
    return f"noise {json.dumps(fields)}"


def test_the_address_group_mode_decodes_the_issues_and_encodes_the_mode_payload() -> (
    None
):
    """Given feedback items, when grouped, then the request is the JSON mode payload and the reply the issues."""
    runner = ScriptedRunner(
        _json(issues=[{"id": "cart", "statement": "dup", "keys": ["k1", "k2"]}])
    )
    items = [{"key": "k1"}, {"key": "k2"}]

    reply = run_agent(address.GROUP_FEEDBACK, address.GroupRequest(items), runner)

    assert reply == address.Grouped(
        (address.FeedbackIssue("cart", "dup", ("k1", "k2")),)
    )
    assert runner.requests[0].prompt.endswith(
        json.dumps({"mode": "group", "items": items})
    )


def test_the_address_resolve_mode_omits_an_absent_statement_and_decodes_the_resolution() -> (
    None
):
    """Given an issue without a statement, when resolved, then the payload omits it and the reply is typed."""
    runner = ScriptedRunner(
        _json(
            verdict="fix-needed",
            evidence=["a.py:1: why"],
            replies={"k1": "Fixed!"},
            reason="done",
        )
    )
    request = address.ResolveRequest(
        address.FeedbackIssue("all", None, ("k1",)), [{"key": "k1"}]
    )

    reply = run_agent(address.RESOLVE_ISSUE, request, runner)

    assert reply == address.Resolved(
        "fix-needed", ("a.py:1: why",), (address.FeedbackReply("k1", "Fixed!"),), "done"
    )
    assert runner.requests[0].prompt.endswith(
        json.dumps(
            {
                "mode": "resolve",
                "issue": {"id": "all", "keys": ["k1"]},
                "items": [{"key": "k1"}],
            }
        )
    )


@pytest.mark.parametrize(
    "output",
    [
        "no json",
        _json(issues="nope"),
        _json(issues=[{"id": "a", "keys": ["k"]}]),
        _json(issues=[{"id": "a", "statement": "s", "keys": "k"}]),
        _json(issues=[{"id": "a", "statement": "s", "keys": [1]}]),
    ],
)
def test_the_address_group_mode_rejects_malformed_output_with_a_reply_error(
    output: str,
) -> None:
    """Given malformed grouping output, when decoded, then a reply error names the address agent."""
    with pytest.raises(AgentReplyError, match="^address agent: "):
        run_agent(
            address.GROUP_FEEDBACK, address.GroupRequest([]), ScriptedRunner(output)
        )


@pytest.mark.parametrize(
    "output",
    [
        _json(verdict="maybe", replies={}),
        _json(verdict="fix-needed"),
        _json(verdict="fix-needed", replies={"k": 1}),
        _json(verdict="fix-needed", replies={}, evidence="a.py:1"),
        _json(verdict="fix-needed", replies={}, reason=3),
    ],
)
def test_the_address_resolve_mode_rejects_malformed_output_with_a_reply_error(
    output: str,
) -> None:
    """Given malformed resolution output, when decoded, then a reply error names the address agent."""
    request = address.ResolveRequest(address.FeedbackIssue("a", "s", ("k",)), [])

    with pytest.raises(AgentReplyError, match="^address agent: "):
        run_agent(address.RESOLVE_ISSUE, request, ScriptedRunner(output))


def test_the_address_profile_denies_publication_and_carries_its_instructions_and_timeout() -> (
    None
):
    """Given the address agent, when it runs, then its profile denies git publication and gh and times out after 1800s."""
    runner = ScriptedRunner(_json(issues=[]))

    run_agent(address.GROUP_FEEDBACK, address.GroupRequest([]), runner)

    profile = runner.profiles[0]
    assert (profile.name, profile.timeout_sec) == ("address", 1800)
    assert profile.permissions.deny_tools == (
        "shell(git commit*)",
        "shell(git push*)",
        "shell(gh*)",
    )
    assert "Handle review feedback" in profile.instructions


# maintainer


def test_the_maintainer_diagnose_mode_decodes_the_classification_and_encodes_the_mode_payload() -> (
    None
):
    """Given a snapshot and logs, when diagnosed, then the request is the JSON mode payload and the reply typed."""
    runner = ScriptedRunner(_json(classification="branch", reason="evidence"))

    reply = run_agent(
        maintainer.DIAGNOSE_CI, maintainer.DiagnoseRequest({"pr": 1}, "log"), runner
    )

    assert reply == maintainer.Diagnosis("branch", "evidence")
    assert runner.requests[0].prompt.endswith(
        json.dumps({"mode": "diagnose", "snapshot": {"pr": 1}, "logs": "log"})
    )


def test_the_maintainer_fix_mode_carries_the_diagnosis_and_decodes_the_verdict() -> (
    None
):
    """Given a diagnosis, when fixed, then the payload nests it and the reply is the verdict."""
    runner = ScriptedRunner(_json(verdict="fixed", reason="fixed cart"))
    request = maintainer.FixRequest(
        maintainer.Diagnosis("branch", "evidence"), {"pr": 1}
    )

    reply = run_agent(maintainer.FIX_CI, request, runner)

    assert reply == maintainer.Fixed("fixed", "fixed cart")
    assert runner.requests[0].prompt.endswith(
        json.dumps(
            {
                "mode": "fix",
                "diagnosis": {"classification": "branch", "reason": "evidence"},
                "snapshot": {"pr": 1},
            }
        )
    )


@pytest.mark.parametrize(
    "output",
    [
        "no json",
        _json(classification="odd"),
        _json(reason="x"),
        _json(classification="branch", reason=3),
    ],
)
def test_the_maintainer_diagnose_mode_rejects_malformed_output_with_a_reply_error(
    output: str,
) -> None:
    """Given malformed diagnosis output, when decoded, then a reply error names the maintainer."""
    with pytest.raises(AgentReplyError, match="^maintainer agent: "):
        run_agent(
            maintainer.DIAGNOSE_CI,
            maintainer.DiagnoseRequest({}, ""),
            ScriptedRunner(output),
        )


@pytest.mark.parametrize("output", ["no json", _json(verdict="odd"), _json(reason="x")])
def test_the_maintainer_fix_mode_rejects_malformed_output_with_a_reply_error(
    output: str,
) -> None:
    """Given malformed fix output, when decoded, then a reply error names the maintainer."""
    request = maintainer.FixRequest(maintainer.Diagnosis("branch"), {})

    with pytest.raises(AgentReplyError, match="^maintainer agent: "):
        run_agent(maintainer.FIX_CI, request, ScriptedRunner(output))

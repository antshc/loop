from __future__ import annotations

import json

import pytest

from workflows.implement_spec.model.reply import ChoreResponse, DevResultError, PlanResponse, ReviewResponse


def _text(status: str = "completed", **result: object) -> str:
    return f"thinking...\n{json.dumps({'status': status, 'result': result})}\nbye"


def test_plan_response_decodes_tickets_in_order_with_their_blockers() -> None:
    plan = PlanResponse().decode(
        _text(tickets=[{"title": "A", "body": "a"}, {"title": "B", "body": "b", "blocked_by": [0]}])
    )

    assert plan.status == "completed"
    assert [(ticket.title, ticket.blocked_by) for ticket in plan.tickets] == [("A", ()), ("B", (0,))]


def test_plan_response_decodes_a_refusal_with_its_reason() -> None:
    plan = PlanResponse().decode(_text("failed", reason="ambiguous"))

    assert (plan.status, plan.reason, plan.tickets) == ("failed", "ambiguous", ())


@pytest.mark.parametrize(
    "text",
    [
        "no json",
        _text(tickets="nope"),
        _text(tickets=["nope"]),
        _text(tickets=[{"title": "A", "blocked_by": ["x"]}]),
        _text("failed"),
        json.dumps({"status": "weird", "result": {}}),
    ],
)
def test_plan_response_rejects_malformed_output(text: str) -> None:
    with pytest.raises(DevResultError):
        PlanResponse().decode(text)


def test_chore_response_is_lenient_about_missing_or_malformed_output() -> None:
    assert ChoreResponse().decode("no json").commit_message == ""
    assert ChoreResponse().decode(_text("failed", reason="x")).summary == ""
    assert ChoreResponse().decode(_text(commit_message=3)).commit_message == ""
    result = ChoreResponse().decode(_text(commit_message="tidy", summary="s", out_of_scope=["old bug"]))
    assert (result.commit_message, result.summary, result.out_of_scope) == ("tidy", "s", ("old bug",))


def test_review_response_decodes_axes_findings_and_the_reviewed_head() -> None:
    result = ReviewResponse().decode(
        _text(
            axes=[{"name": "bugs", "outcome": "completed"}, {"name": "smells", "outcome": "FAILED", "note": "n"}],
            findings=[{"axis": "bugs", "kind": "Bug", "summary": "npe"}],
            posted=["c1", 7],
            head="abc",
        )
    )

    assert not result.completed and result.head_sha == "abc" and result.posted == ("c1", "7")
    assert [finding.summary for finding in result.blocking_findings] == ["npe"]


def test_review_response_rejects_a_failed_review() -> None:
    with pytest.raises(DevResultError, match="review failed: no skill"):
        ReviewResponse().decode(_text("failed", reason="no skill"))

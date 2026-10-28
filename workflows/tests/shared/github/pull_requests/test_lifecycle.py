from __future__ import annotations

from dataclasses import replace

import pytest

from workflows.shared.github.pull_requests import LifecycleAction, LifecycleDecision, PullRequestLifecyclePolicy
from workflows.shared.github.pull_requests.model import PullRequestState
from workflows.tests.shared.builders import build_pull_request_snapshot

CLOSE, REOPEN = LifecycleAction.CLOSE, LifecycleAction.REOPEN
VALID, NO_OP, INVALID = LifecycleDecision.VALID, LifecycleDecision.NO_OP, LifecycleDecision.INVALID


@pytest.mark.parametrize(
    ("state", "merged", "action", "expected"),
    [
        (PullRequestState.OPEN, False, CLOSE, VALID),
        (PullRequestState.CLOSED, False, CLOSE, NO_OP),
        (PullRequestState.CLOSED, True, CLOSE, NO_OP),
        (PullRequestState.OPEN, False, REOPEN, NO_OP),
        (PullRequestState.CLOSED, False, REOPEN, VALID),
        (PullRequestState.CLOSED, True, REOPEN, INVALID),
    ],
    ids=["close-open", "close-closed", "close-merged", "reopen-open", "reopen-closed", "reopen-merged"],
)
def test_close_and_reopen_are_decided_from_the_pull_request_state(
    state: PullRequestState, merged: bool, action: LifecycleAction, expected: LifecycleDecision
) -> None:
    pull = replace(build_pull_request_snapshot(state=state).pull_request, merged=merged)

    assert PullRequestLifecyclePolicy().decide(pull, action) is expected

from __future__ import annotations

import pytest

from workflows.software_factory.infrastructure.github.pull_requests.model import (
    CheckKind,
    CheckOutcome,
    CheckResult,
    Mergeability,
    MergeState,
)


@pytest.mark.parametrize(
    ("computed", "expected"),
    [
        (True, Mergeability.MERGEABLE),
        (False, Mergeability.CONFLICTING),
        (None, Mergeability.UNKNOWN),
    ],
)
def test_mergeability_is_unknown_until_github_computes_it(
    computed: bool | None, expected: Mergeability
) -> None:
    assert Mergeability.of(computed) is expected


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("clean", MergeState.CLEAN),
        ("dirty", MergeState.DIRTY),
        ("has_hooks", MergeState.HAS_HOOKS),
        ("unknown", MergeState.UNKNOWN),
        ("a state GitHub adds later", MergeState.UNKNOWN),
    ],
)
def test_a_merge_state_github_does_not_name_is_unknown(
    reported: str, expected: MergeState
) -> None:
    assert MergeState(reported) is expected


def _check(commit: str, outcome: CheckOutcome) -> CheckResult:
    return CheckResult(CheckKind.CHECK_RUN, 1, "test", commit, outcome)


@pytest.mark.parametrize(
    ("commit", "outcome", "stale", "passing"),
    [
        ("head", CheckOutcome.PASSED, False, True),
        ("head", CheckOutcome.PENDING, False, False),
        ("head", CheckOutcome.FAILED, False, False),
        ("older", CheckOutcome.PASSED, True, False),
        ("older", CheckOutcome.FAILED, True, False),
    ],
)
def test_a_check_result_passes_only_for_the_head_commit_it_ran_against(
    commit: str, outcome: CheckOutcome, stale: bool, passing: bool
) -> None:
    check = _check(commit, outcome)

    assert (check.stale("head"), check.passing("head")) == (stale, passing)

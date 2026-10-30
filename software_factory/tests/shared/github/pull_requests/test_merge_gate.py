from __future__ import annotations

import pytest

from software_factory.shared.github.pull_requests import (
    MergeGateEvaluator,
    MergePolicySnapshot,
    MergeReadiness,
    ReadinessStatus,
    ReasonCode,
)
from software_factory.shared.github.pull_requests.merge_gate import STATUS_OF_REASON, MergeReason
from software_factory.shared.github.pull_requests.merge_policy import PolicySource
from software_factory.shared.github.pull_requests.model import (
    CheckKind,
    CheckOutcome,
    Mergeability,
    MergeState,
    PullRequestSnapshot,
    PullRequestState,
    ReviewVerdict,
)
from tests.shared.builders import build_check, build_pull_request_snapshot, build_review, build_thread


READY, WAITING, BLOCKED, UNKNOWN = ReadinessStatus.READY, ReadinessStatus.WAITING, ReadinessStatus.BLOCKED, ReadinessStatus.UNKNOWN

_POLICY = MergePolicySnapshot("main", required_checks=frozenset({"test"}), required_approvals=1, conversation_resolution=True)
_APPROVAL = build_review(1, "bob", ReviewVerdict.APPROVED)


def _ready() -> PullRequestSnapshot:
    return build_pull_request_snapshot(reviews=(_APPROVAL,), checks=(build_check("test"),))


def _evaluate(snapshot: PullRequestSnapshot, policy: MergePolicySnapshot = _POLICY) -> MergeReadiness:
    return MergeGateEvaluator().evaluate(snapshot, policy)


def test_a_pull_request_meeting_every_requirement_is_ready_with_no_reason() -> None:
    readiness = _evaluate(_ready())

    assert readiness == MergeReadiness(READY, ()) and readiness.ready


def test_a_pull_request_under_an_empty_policy_is_ready() -> None:
    assert _evaluate(build_pull_request_snapshot(), MergePolicySnapshot("main")).ready


def _change(**overrides: object) -> PullRequestSnapshot:
    return build_pull_request_snapshot(**{"reviews": (_APPROVAL,), "checks": (build_check("test"),), **overrides})


@pytest.mark.parametrize(
    ("snapshot", "status", "code"),
    [
        pytest.param(_change(state=PullRequestState.CLOSED), BLOCKED, ReasonCode.NOT_OPEN, id="closed"),
        pytest.param(_change(draft=True), BLOCKED, ReasonCode.DRAFT, id="draft"),
        pytest.param(_change(mergeability=Mergeability.CONFLICTING), BLOCKED, ReasonCode.CONFLICTS, id="conflicts"),
        pytest.param(_change(mergeability=Mergeability.UNKNOWN), WAITING, ReasonCode.MERGEABILITY_UNKNOWN, id="mergeability-unknown"),
        pytest.param(_change(checks=()), WAITING, ReasonCode.CHECK_MISSING, id="check-missing"),
        pytest.param(_change(checks=(build_check("lint"),)), WAITING, ReasonCode.CHECK_MISSING, id="other-check-only"),
        pytest.param(_change(checks=(build_check("test", CheckOutcome.FAILED),)), BLOCKED, ReasonCode.CHECK_FAILING, id="check-failing"),
        pytest.param(_change(checks=(build_check("test", CheckOutcome.PENDING),)), WAITING, ReasonCode.CHECK_PENDING, id="check-pending"),
        pytest.param(_change(checks=(build_check("test", commit="older"),)), WAITING, ReasonCode.CHECK_STALE, id="check-stale"),
        pytest.param(_change(reviews=()), BLOCKED, ReasonCode.APPROVALS_MISSING, id="approvals-missing"),
        pytest.param(
            _change(reviews=(_APPROVAL, build_review(2, "carol", ReviewVerdict.CHANGES_REQUESTED))),
            BLOCKED,
            ReasonCode.CHANGES_REQUESTED,
            id="changes-requested",
        ),
        pytest.param(_change(threads=(build_thread(),)), BLOCKED, ReasonCode.UNRESOLVED_THREADS, id="unresolved-thread"),
    ],
)
def test_one_unmet_condition_gives_its_status_and_its_one_reason_code(
    snapshot: PullRequestSnapshot, status: ReadinessStatus, code: ReasonCode
) -> None:
    readiness = _evaluate(snapshot)

    assert (readiness.status, readiness.codes) == (status, (code,))
    assert not readiness.ready


def test_every_reason_code_has_a_status_and_none_of_them_is_ready() -> None:
    assert set(STATUS_OF_REASON) == set(ReasonCode)
    assert READY not in STATUS_OF_REASON.values()


def test_a_denied_policy_source_makes_readiness_unknown_even_when_everything_else_is_met() -> None:
    policy = MergePolicySnapshot("main", unavailable=(PolicySource.RULESETS,))

    readiness = _evaluate(_ready(), policy)

    assert readiness == MergeReadiness(UNKNOWN, (MergeReason(ReasonCode.POLICY_UNAVAILABLE, "rulesets"),))


def test_each_denied_policy_source_is_its_own_reason() -> None:
    policy = MergePolicySnapshot("main", unavailable=(PolicySource.RULESETS, PolicySource.BRANCH_PROTECTION))

    assert _evaluate(_ready(), policy).codes == (ReasonCode.POLICY_UNAVAILABLE, ReasonCode.POLICY_UNAVAILABLE)


@pytest.mark.parametrize(
    ("snapshot", "policy", "subject"),
    [
        pytest.param(_ready(), MergePolicySnapshot("release"), "release", id="policy-of-another-branch"),
        pytest.param(build_pull_request_snapshot(head_sha=""), MergePolicySnapshot("main"), "head commit", id="no-head-commit"),
        pytest.param(build_pull_request_snapshot(), MergePolicySnapshot("main", code_owner_review=True), "review decision", id="no-review-decision"),
    ],
)
def test_an_incomplete_input_makes_readiness_unknown(snapshot: PullRequestSnapshot, policy: MergePolicySnapshot, subject: str) -> None:
    readiness = _evaluate(snapshot, policy)

    assert readiness.status is UNKNOWN and readiness.codes == (ReasonCode.INCOMPLETE_INPUT,)
    assert subject in readiness.reasons[0].subject


@pytest.mark.parametrize(
    ("snapshot", "status", "codes"),
    [
        pytest.param(_change(draft=True, checks=(build_check("test", CheckOutcome.PENDING),)), BLOCKED, (ReasonCode.DRAFT, ReasonCode.CHECK_PENDING), id="blocked-over-waiting"),
        pytest.param(_change(mergeability=Mergeability.UNKNOWN, checks=(build_check("test", CheckOutcome.PENDING),)), WAITING, (ReasonCode.MERGEABILITY_UNKNOWN, ReasonCode.CHECK_PENDING), id="two-waits"),
        pytest.param(_change(reviews=(), checks=()), BLOCKED, (ReasonCode.CHECK_MISSING, ReasonCode.APPROVALS_MISSING), id="missing-approvals-and-check"),
        pytest.param(_change(draft=True, mergeability=Mergeability.CONFLICTING), BLOCKED, (ReasonCode.DRAFT, ReasonCode.CONFLICTS), id="draft-and-conflicts"),
    ],
)
def test_every_unmet_condition_is_reported_and_the_most_decisive_sets_the_status(
    snapshot: PullRequestSnapshot, status: ReadinessStatus, codes: tuple[ReasonCode, ...]
) -> None:
    readiness = _evaluate(snapshot)

    assert (readiness.status, readiness.codes) == (status, codes)


def test_blocked_outranks_unknown_which_outranks_waiting() -> None:
    policy = MergePolicySnapshot("main", required_checks=frozenset({"test"}), unavailable=(PolicySource.BRANCH_PROTECTION,))
    pending = build_pull_request_snapshot(checks=(build_check("test", CheckOutcome.PENDING),))

    assert _evaluate(pending, policy).status is UNKNOWN
    assert _evaluate(build_pull_request_snapshot(draft=True, checks=pending.checks), policy).status is BLOCKED


def test_each_required_check_is_judged_on_its_own() -> None:
    policy = MergePolicySnapshot("main", required_checks=frozenset({"build", "lint", "test"}))
    checks = (build_check("build"), build_check("lint", CheckOutcome.FAILED, id=2), build_check("test", commit="older", id=3))

    readiness = _evaluate(build_pull_request_snapshot(checks=checks), policy)

    assert readiness.reasons == (MergeReason(ReasonCode.CHECK_FAILING, "lint"), MergeReason(ReasonCode.CHECK_STALE, "test"))


def test_a_check_that_is_not_required_never_holds_up_readiness() -> None:
    snapshot = _change(checks=(build_check("test"), build_check("coverage", CheckOutcome.FAILED, id=2)))

    assert _evaluate(snapshot).ready


def test_a_workflow_run_does_not_stand_in_for_a_required_check() -> None:
    snapshot = _change(checks=(build_check("test", kind=CheckKind.WORKFLOW_RUN),))

    assert _evaluate(snapshot).codes == (ReasonCode.CHECK_MISSING,)


def test_a_commit_status_satisfies_a_required_check() -> None:
    assert _evaluate(_change(checks=(build_check("test", kind=CheckKind.STATUS),))).ready


@pytest.mark.parametrize("earlier", [CheckOutcome.PENDING, CheckOutcome.FAILED])
def test_only_the_newest_status_of_a_context_counts(earlier: CheckOutcome) -> None:
    statuses = (
        build_check("test", CheckOutcome.PASSED, kind=CheckKind.STATUS, id=9),
        build_check("test", earlier, kind=CheckKind.STATUS, id=3),
    )

    assert _evaluate(_change(checks=statuses)).ready


def test_a_newer_failing_status_outranks_an_earlier_passing_one() -> None:
    statuses = (
        build_check("test", CheckOutcome.PASSED, kind=CheckKind.STATUS, id=3),
        build_check("test", CheckOutcome.FAILED, kind=CheckKind.STATUS, id=9),
    )

    assert _evaluate(_change(checks=statuses)).codes == (ReasonCode.CHECK_FAILING,)


def test_approvals_count_each_reviewer_once_by_their_latest_decisive_review() -> None:
    policy = MergePolicySnapshot("main", required_approvals=2)
    reviews = (
        build_review(1, "bob", ReviewVerdict.APPROVED),
        build_review(2, "bob", ReviewVerdict.APPROVED),
        build_review(3, "carol", ReviewVerdict.APPROVED),
        build_review(4, "carol", ReviewVerdict.COMMENTED),
        build_review(5, "dave", ReviewVerdict.APPROVED),
        build_review(6, "dave", ReviewVerdict.DISMISSED),
    )

    readiness = _evaluate(build_pull_request_snapshot(reviews=reviews), policy)

    assert readiness.ready


def test_a_dismissed_approval_no_longer_counts() -> None:
    reviews = (build_review(1, "bob", ReviewVerdict.APPROVED), build_review(2, "bob", ReviewVerdict.DISMISSED))

    assert _evaluate(build_pull_request_snapshot(reviews=reviews), MergePolicySnapshot("main", required_approvals=1)).codes == (
        ReasonCode.APPROVALS_MISSING,
    )


def test_a_later_approval_clears_an_earlier_request_for_changes() -> None:
    reviews = (build_review(1, "bob", ReviewVerdict.CHANGES_REQUESTED), build_review(2, "bob", ReviewVerdict.APPROVED))

    assert _evaluate(build_pull_request_snapshot(reviews=reviews), MergePolicySnapshot("main", required_approvals=1)).ready


def test_a_request_for_changes_blocks_even_when_the_policy_asks_for_no_approvals() -> None:
    reviews = (build_review(1, "bob", ReviewVerdict.CHANGES_REQUESTED),)

    readiness = _evaluate(build_pull_request_snapshot(reviews=reviews), MergePolicySnapshot("main"))

    assert (readiness.status, readiness.codes) == (BLOCKED, (ReasonCode.CHANGES_REQUESTED,))


def test_the_author_cannot_approve_their_own_pull_request() -> None:
    reviews = (build_review(1, "me", ReviewVerdict.APPROVED),)

    snapshot = build_pull_request_snapshot(author="me", reviews=reviews)

    assert _evaluate(snapshot, MergePolicySnapshot("main", required_approvals=1)).codes == (ReasonCode.APPROVALS_MISSING,)


def test_an_approval_of_an_older_commit_counts_unless_the_policy_dismisses_stale_reviews() -> None:
    snapshot = build_pull_request_snapshot(reviews=(build_review(1, "bob", ReviewVerdict.APPROVED, commit="older"),))

    assert _evaluate(snapshot, MergePolicySnapshot("main", required_approvals=1)).ready
    assert _evaluate(snapshot, MergePolicySnapshot("main", required_approvals=1, dismiss_stale_reviews=True)).codes == (
        ReasonCode.APPROVALS_MISSING,
    )


@pytest.mark.parametrize(
    ("decision", "codes"),
    [("APPROVED", ()), ("REVIEW_REQUIRED", (ReasonCode.CODE_OWNER_REVIEW_MISSING,)), ("", (ReasonCode.INCOMPLETE_INPUT,))],
)
def test_a_required_code_owner_review_is_read_from_github_review_decision(decision: str, codes: tuple[ReasonCode, ...]) -> None:
    policy = MergePolicySnapshot("main", code_owner_review=True)

    assert _evaluate(build_pull_request_snapshot(review_decision=decision), policy).codes == codes


def test_resolved_threads_and_threads_not_required_to_be_resolved_do_not_block() -> None:
    open_thread, resolved = build_thread("t1"), build_thread("t2", resolved=True)

    assert _evaluate(_change(threads=(resolved,))).ready
    assert _evaluate(_change(threads=(open_thread,)), MergePolicySnapshot("main", required_approvals=1)).ready


def test_a_branch_behind_its_base_blocks_only_when_the_policy_requires_it_up_to_date() -> None:
    behind = build_pull_request_snapshot(merge_state=MergeState.BEHIND)

    assert _evaluate(behind, MergePolicySnapshot("main")).ready
    assert _evaluate(behind, MergePolicySnapshot("main", up_to_date=True)).codes == (ReasonCode.BRANCH_BEHIND,)


def test_evaluating_leaves_the_snapshots_unchanged() -> None:
    snapshot = _ready()

    _evaluate(snapshot)

    assert snapshot == _ready()

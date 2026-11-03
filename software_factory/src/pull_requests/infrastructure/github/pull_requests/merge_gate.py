"""Whether a pull request is ready to merge, and why not: a pure evaluation of loaded snapshots that never merges."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .merge_policy import MergePolicySnapshot
from .model import (
    CheckKind,
    CheckOutcome,
    CheckResult,
    Mergeability,
    MergeState,
    PullRequestSnapshot,
    ReviewDecision,
    ReviewVerdict,
)


class ReadinessStatus(StrEnum):
    """READY: nothing stands in the way. WAITING: only time can resolve what is left. BLOCKED: something must change. UNKNOWN: an input is unseen."""

    READY = "ready"
    WAITING = "waiting"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class ReasonCode(StrEnum):
    """One unmet merge condition."""

    NOT_OPEN = "not_open"
    DRAFT = "draft"
    CONFLICTS = "conflicts"
    MERGEABILITY_UNKNOWN = "mergeability_unknown"
    BRANCH_BEHIND = "branch_behind"
    CHECK_MISSING = "check_missing"
    CHECK_FAILING = "check_failing"
    CHECK_PENDING = "check_pending"
    CHECK_STALE = "check_stale"
    APPROVALS_MISSING = "approvals_missing"
    CODE_OWNER_REVIEW_MISSING = "code_owner_review_missing"
    CHANGES_REQUESTED = "changes_requested"
    UNRESOLVED_THREADS = "unresolved_threads"
    POLICY_UNAVAILABLE = "policy_unavailable"
    INCOMPLETE_INPUT = "incomplete_input"


# What each unmet condition makes the pull request: a check that is not yet reported on the head commit is expected to arrive, so it waits.
STATUS_OF_REASON: dict[ReasonCode, ReadinessStatus] = {
    ReasonCode.NOT_OPEN: ReadinessStatus.BLOCKED,
    ReasonCode.DRAFT: ReadinessStatus.BLOCKED,
    ReasonCode.CONFLICTS: ReadinessStatus.BLOCKED,
    ReasonCode.MERGEABILITY_UNKNOWN: ReadinessStatus.WAITING,
    ReasonCode.BRANCH_BEHIND: ReadinessStatus.BLOCKED,
    ReasonCode.CHECK_MISSING: ReadinessStatus.WAITING,
    ReasonCode.CHECK_FAILING: ReadinessStatus.BLOCKED,
    ReasonCode.CHECK_PENDING: ReadinessStatus.WAITING,
    ReasonCode.CHECK_STALE: ReadinessStatus.WAITING,
    ReasonCode.APPROVALS_MISSING: ReadinessStatus.BLOCKED,
    ReasonCode.CODE_OWNER_REVIEW_MISSING: ReadinessStatus.BLOCKED,
    ReasonCode.CHANGES_REQUESTED: ReadinessStatus.BLOCKED,
    ReasonCode.UNRESOLVED_THREADS: ReadinessStatus.BLOCKED,
    ReasonCode.POLICY_UNAVAILABLE: ReadinessStatus.UNKNOWN,
    ReasonCode.INCOMPLETE_INPUT: ReadinessStatus.UNKNOWN,
}

# A verdict that is certain beats an unseen input, which beats a wait that time resolves.
_PRECEDENCE = (
    ReadinessStatus.BLOCKED,
    ReadinessStatus.UNKNOWN,
    ReadinessStatus.WAITING,
)

_REQUIRED_CHECK_KINDS = (CheckKind.CHECK_RUN, CheckKind.STATUS)

_DECISIVE_VERDICTS = (
    ReviewVerdict.APPROVED,
    ReviewVerdict.CHANGES_REQUESTED,
    ReviewVerdict.DISMISSED,
)


@dataclass(frozen=True)
class MergeReason:
    """An unmet condition; `subject` names what it is about, such as a check."""

    code: ReasonCode
    subject: str = ""

    @property
    def status(self) -> ReadinessStatus:
        """The readiness this condition alone gives."""
        return STATUS_OF_REASON[self.code]


@dataclass(frozen=True)
class MergeReadiness:
    """READY only with no unmet condition; otherwise the status the most decisive reason gives, with one reason per unmet condition."""

    status: ReadinessStatus
    reasons: tuple[MergeReason, ...] = ()

    @property
    def ready(self) -> bool:
        """Nothing stands in the way of merging."""
        return self.status is ReadinessStatus.READY

    @property
    def codes(self) -> tuple[ReasonCode, ...]:
        """The reason codes, in the order the conditions were checked."""
        return tuple(reason.code for reason in self.reasons)


class MergeGateEvaluator:
    """Evaluates merge readiness from loaded snapshots; it merges nothing and reads nothing."""

    def evaluate(
        self, snapshot: PullRequestSnapshot, policy: MergePolicySnapshot
    ) -> MergeReadiness:
        """The readiness of the pull request under `policy`; unknown or incomplete input never gives READY."""
        reasons = (
            *self._state(snapshot),
            *self._policy_inputs(snapshot, policy),
            *self._checks(snapshot, policy),
            *self._reviews(snapshot, policy),
            *self._threads(snapshot, policy),
        )
        return MergeReadiness(_status(reasons), reasons)

    @staticmethod
    def _state(snapshot: PullRequestSnapshot) -> list[MergeReason]:
        pull = snapshot.pull_request
        reasons = []
        if not pull.is_open:
            reasons.append(MergeReason(ReasonCode.NOT_OPEN))
        if pull.draft:
            reasons.append(MergeReason(ReasonCode.DRAFT))
        if pull.mergeability is Mergeability.CONFLICTING:
            reasons.append(MergeReason(ReasonCode.CONFLICTS))
        if pull.mergeability is Mergeability.UNKNOWN:
            reasons.append(MergeReason(ReasonCode.MERGEABILITY_UNKNOWN))
        return reasons

    @staticmethod
    def _policy_inputs(
        snapshot: PullRequestSnapshot, policy: MergePolicySnapshot
    ) -> list[MergeReason]:
        pull = snapshot.pull_request
        reasons = [
            MergeReason(ReasonCode.POLICY_UNAVAILABLE, source)
            for source in policy.unavailable
        ]
        if policy.base_ref != pull.base_ref:
            reasons.append(
                MergeReason(
                    ReasonCode.INCOMPLETE_INPUT,
                    f"policy of {policy.base_ref}, base is {pull.base_ref}",
                )
            )
        if not pull.head_sha:
            reasons.append(MergeReason(ReasonCode.INCOMPLETE_INPUT, "head commit"))
        if (
            policy.code_owner_review
            and snapshot.review_decision is ReviewDecision.UNKNOWN
        ):
            reasons.append(MergeReason(ReasonCode.INCOMPLETE_INPUT, "review decision"))
        if policy.up_to_date and pull.merge_state is MergeState.BEHIND:
            reasons.append(MergeReason(ReasonCode.BRANCH_BEHIND))
        return reasons

    @classmethod
    def _checks(
        cls, snapshot: PullRequestSnapshot, policy: MergePolicySnapshot
    ) -> list[MergeReason]:
        reasons = []
        for name in sorted(policy.required_checks):
            code = cls._check_problem(snapshot, name)
            if code is not None:
                reasons.append(MergeReason(code, name))
        return reasons

    @staticmethod
    def _check_problem(snapshot: PullRequestSnapshot, name: str) -> ReasonCode | None:
        head = snapshot.pull_request.head_sha
        named = _latest_statuses(
            [
                check
                for check in snapshot.checks
                if check.name == name and check.kind in _REQUIRED_CHECK_KINDS
            ]
        )
        current = [check for check in named if not check.stale(head)]
        if any(check.outcome is CheckOutcome.FAILED for check in current):
            return ReasonCode.CHECK_FAILING
        if any(check.outcome is CheckOutcome.PENDING for check in current):
            return ReasonCode.CHECK_PENDING
        if current:
            return None
        return ReasonCode.CHECK_STALE if named else ReasonCode.CHECK_MISSING

    @classmethod
    def _reviews(
        cls, snapshot: PullRequestSnapshot, policy: MergePolicySnapshot
    ) -> list[MergeReason]:
        reasons = []
        verdicts = cls._verdicts(snapshot, policy)
        if ReviewVerdict.CHANGES_REQUESTED in verdicts.values():
            reasons.append(MergeReason(ReasonCode.CHANGES_REQUESTED))
        approvals = sum(
            1 for verdict in verdicts.values() if verdict is ReviewVerdict.APPROVED
        )
        if approvals < policy.required_approvals:
            reasons.append(
                MergeReason(
                    ReasonCode.APPROVALS_MISSING,
                    f"{approvals} of {policy.required_approvals}",
                )
            )
        if (
            policy.code_owner_review
            and snapshot.review_decision is ReviewDecision.REVIEW_REQUIRED
        ):
            reasons.append(MergeReason(ReasonCode.CODE_OWNER_REVIEW_MISSING))
        return reasons

    @staticmethod
    def _verdicts(
        snapshot: PullRequestSnapshot, policy: MergePolicySnapshot
    ) -> dict[str, ReviewVerdict]:
        """Each reviewer's standing verdict: their latest approval, request for changes, or dismissal; the author never approves their own change."""
        head = snapshot.pull_request.head_sha
        latest = {}
        for review in sorted(snapshot.reviews, key=lambda review: review.id):
            if (
                review.verdict in _DECISIVE_VERDICTS
                and review.reviewer != snapshot.pull_request.author
            ):
                latest[review.reviewer] = review
        return {
            reviewer: ReviewVerdict.DISMISSED
            if _outdated(review.verdict, review.commit, head, policy)
            else review.verdict
            for reviewer, review in latest.items()
        }

    @staticmethod
    def _threads(
        snapshot: PullRequestSnapshot, policy: MergePolicySnapshot
    ) -> list[MergeReason]:
        unresolved = [thread for thread in snapshot.threads if not thread.resolved]
        if policy.conversation_resolution and unresolved:
            return [MergeReason(ReasonCode.UNRESOLVED_THREADS, str(len(unresolved)))]
        return []


def _latest_statuses(checks: list[CheckResult]) -> list[CheckResult]:
    """`checks` with only the newest commit status of each commit; the statuses endpoint lists every status ever posted, so an earlier `pending` or `failure` is superseded."""
    newest: dict[str, CheckResult] = {}
    for check in checks:
        if check.kind is CheckKind.STATUS and (
            check.commit not in newest or check.id > newest[check.commit].id
        ):
            newest[check.commit] = check
    return [
        check
        for check in checks
        if check.kind is not CheckKind.STATUS or newest[check.commit] is check
    ]


def _outdated(
    verdict: ReviewVerdict, commit: str, head: str, policy: MergePolicySnapshot
) -> bool:
    # A push dismisses approvals, never requests for changes, when the policy says so.
    return (
        policy.dismiss_stale_reviews
        and verdict is ReviewVerdict.APPROVED
        and commit != head
    )


def _status(reasons: tuple[MergeReason, ...]) -> ReadinessStatus:
    if not reasons:
        return ReadinessStatus.READY
    statuses = {reason.status for reason in reasons}
    return next(status for status in _PRECEDENCE if status in statuses)

"""The merge policy of a base branch as an immutable snapshot, and the rules that read it from GitHub's ruleset and branch-protection payloads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PolicySource(StrEnum):
    """Where merge requirements come from."""

    RULESETS = "rulesets"
    BRANCH_PROTECTION = "branch_protection"


@dataclass(frozen=True)
class MergePolicySnapshot:
    """What merging into `base_ref` requires; `unavailable` names the sources GitHub denied, so the policy is unseen rather than empty."""

    base_ref: str
    required_checks: frozenset[str] = frozenset()
    required_approvals: int = 0
    code_owner_review: bool = False
    dismiss_stale_reviews: bool = False
    conversation_resolution: bool = False
    linear_history: bool = False
    up_to_date: bool = False
    unavailable: tuple[PolicySource, ...] = ()

    @property
    def available(self) -> bool:
        """Every policy source was readable."""
        return not self.unavailable

    def stricter(self, other: MergePolicySnapshot) -> MergePolicySnapshot:
        """The combined requirement of both policies: every check, the larger approval count, and every switch either turns on."""
        return MergePolicySnapshot(
            base_ref=self.base_ref,
            required_checks=self.required_checks | other.required_checks,
            required_approvals=max(self.required_approvals, other.required_approvals),
            code_owner_review=self.code_owner_review or other.code_owner_review,
            dismiss_stale_reviews=self.dismiss_stale_reviews or other.dismiss_stale_reviews,
            conversation_resolution=self.conversation_resolution or other.conversation_resolution,
            linear_history=self.linear_history or other.linear_history,
            up_to_date=self.up_to_date or other.up_to_date,
            unavailable=tuple(dict.fromkeys((*self.unavailable, *other.unavailable))),
        )


def unavailable_policy(base_ref: str, source: PolicySource) -> MergePolicySnapshot:
    """The policy of a source GitHub refused to show."""
    return MergePolicySnapshot(base_ref, unavailable=(source,))


def policy_from_rules(base_ref: str, rules: Iterable[Mapping[str, Any]]) -> MergePolicySnapshot:
    """The requirements of the repository rules that apply to `base_ref`, the strictest wherever several rulesets name the same one."""
    policy = MergePolicySnapshot(base_ref)
    for rule in rules:
        policy = policy.stricter(_rule_policy(base_ref, rule))
    return policy


def policy_from_protection(base_ref: str, protection: Mapping[str, Any]) -> MergePolicySnapshot:
    """The requirements of the classic branch protection of `base_ref`."""
    checks = protection.get("required_status_checks") or {}
    reviews = protection.get("required_pull_request_reviews") or {}
    return MergePolicySnapshot(
        base_ref,
        required_checks=_protection_checks(checks),
        required_approvals=reviews.get("required_approving_review_count") or 0,
        code_owner_review=bool(reviews.get("require_code_owner_reviews")),
        dismiss_stale_reviews=bool(reviews.get("dismiss_stale_reviews")),
        conversation_resolution=_enabled(protection, "required_conversation_resolution"),
        linear_history=_enabled(protection, "required_linear_history"),
        up_to_date=bool(checks.get("strict")),
    )


def _rule_policy(base_ref: str, rule: Mapping[str, Any]) -> MergePolicySnapshot:
    parameters = rule.get("parameters") or {}
    match rule.get("type"):
        case "pull_request":
            return MergePolicySnapshot(
                base_ref,
                required_approvals=parameters.get("required_approving_review_count") or 0,
                code_owner_review=bool(parameters.get("require_code_owner_review")),
                dismiss_stale_reviews=bool(parameters.get("dismiss_stale_reviews_on_push")),
                conversation_resolution=bool(parameters.get("required_review_thread_resolution")),
            )
        case "required_status_checks":
            checks = frozenset(check["context"] for check in parameters.get("required_status_checks") or ())
            return MergePolicySnapshot(base_ref, required_checks=checks, up_to_date=bool(parameters.get("strict_required_status_checks_policy")))
        case "required_linear_history":
            return MergePolicySnapshot(base_ref, linear_history=True)
        case _:
            return MergePolicySnapshot(base_ref)


def _protection_checks(checks: Mapping[str, Any]) -> frozenset[str]:
    # `contexts` is the legacy list of the same checks that `checks` names with their app.
    names = [*(checks.get("contexts") or ()), *(check["context"] for check in checks.get("checks") or ())]
    return frozenset(names)


def _enabled(protection: Mapping[str, Any], key: str) -> bool:
    return bool((protection.get(key) or {}).get("enabled"))

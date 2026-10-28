from __future__ import annotations

import pytest

from workflows.shared.github.account import GitHub
from workflows.shared.github.pull_requests import MergePolicyReader, MergePolicySnapshot
from workflows.shared.github.pull_requests.merge_policy import PolicySource
from workflows.shared.process import CommandError
from workflows.tests.shared.fakes import (
    FakeGhCli,
    FakePullRequestHost,
    classic_protection,
    pull_request_rule,
    required_checks_rule,
)


_RULESETS = ("api", "--paginate", "--slurp", "repos/a/b/rules/branches/main")
_PROTECTION = ("api", "repos/a/b/branches/main/protection")


def _read(host: FakePullRequestHost) -> tuple[MergePolicyReader, FakeGhCli]:
    gh = FakeGhCli(pulls=host)
    return MergePolicyReader(GitHub(gh).repository("a/b")), gh


def test_a_branch_with_neither_rulesets_nor_protection_has_an_empty_available_policy() -> None:
    policy = _read(FakePullRequestHost())[0].policy("main")

    assert policy == MergePolicySnapshot("main") and policy.available


def test_the_policy_is_read_from_both_rulesets_and_classic_protection_of_the_base_branch() -> None:
    host = FakePullRequestHost()
    host.rules["main"] = [pull_request_rule(1)]
    host.protection["main"] = classic_protection(checks=("test",))
    reader, gh = _read(host)

    reader.policy("main")

    assert gh.calls_matching("api", "--paginate", "--slurp", "repos/a/b/rules/branches/main")
    assert gh.calls_matching("api", "repos/a/b/branches/main/protection")


def test_rulesets_alone_give_the_policy() -> None:
    host = FakePullRequestHost()
    host.rules["main"] = [
        pull_request_rule(2, code_owners=True, dismiss_stale=True, threads=True),
        required_checks_rule("lint", "test", strict=True),
        {"type": "required_linear_history"},
        {"type": "non_fast_forward"},
    ]

    policy = _read(host)[0].policy("main")

    assert policy == MergePolicySnapshot(
        "main",
        required_checks=frozenset({"lint", "test"}),
        required_approvals=2,
        code_owner_review=True,
        dismiss_stale_reviews=True,
        conversation_resolution=True,
        linear_history=True,
        up_to_date=True,
    )


def test_classic_protection_alone_gives_the_policy() -> None:
    host = FakePullRequestHost()
    host.protection["main"] = classic_protection(
        3, checks=("build",), strict=True, code_owners=True, dismiss_stale=True, linear_history=True, conversation_resolution=True
    )

    policy = _read(host)[0].policy("main")

    assert policy == MergePolicySnapshot(
        "main",
        required_checks=frozenset({"build"}),
        required_approvals=3,
        code_owner_review=True,
        dismiss_stale_reviews=True,
        conversation_resolution=True,
        linear_history=True,
        up_to_date=True,
    )


def test_protection_with_no_review_or_check_requirement_requires_neither() -> None:
    host = FakePullRequestHost()
    host.protection["main"] = classic_protection(conversation_resolution=True)

    assert _read(host)[0].policy("main") == MergePolicySnapshot("main", conversation_resolution=True)


def test_when_both_apply_the_stricter_combined_requirement_is_used() -> None:
    host = FakePullRequestHost()
    host.rules["main"] = [pull_request_rule(1, threads=True), required_checks_rule("lint")]
    host.protection["main"] = classic_protection(2, checks=("test",), linear_history=True)

    policy = _read(host)[0].policy("main")

    assert policy == MergePolicySnapshot(
        "main",
        required_checks=frozenset({"lint", "test"}),
        required_approvals=2,
        conversation_resolution=True,
        linear_history=True,
    )


def test_several_rulesets_on_the_branch_combine_to_the_strictest_rule() -> None:
    host = FakePullRequestHost()
    host.rules["main"] = [pull_request_rule(1), pull_request_rule(3), required_checks_rule("a"), required_checks_rule("b")]

    policy = _read(host)[0].policy("main")

    assert (policy.required_approvals, policy.required_checks) == (3, frozenset({"a", "b"}))


def test_the_policy_of_another_branch_is_not_applied() -> None:
    host = FakePullRequestHost()
    host.rules["release"] = [pull_request_rule(2)]
    host.protection["release"] = classic_protection(2)

    assert _read(host)[0].policy("main") == MergePolicySnapshot("main")


def test_a_branch_name_with_a_slash_is_addressed_as_a_path() -> None:
    host = FakePullRequestHost()
    host.rules["release/1.0"] = [pull_request_rule(2)]
    reader, gh = _read(host)

    assert reader.policy("release/1.0").required_approvals == 2
    assert gh.calls_matching("api", "repos/a/b/branches/release/1.0/protection")


def test_denied_rulesets_leave_the_policy_unavailable_while_protection_is_still_read() -> None:
    host = FakePullRequestHost()
    host.protection["main"] = classic_protection(2)
    reader, gh = _read(host)
    gh.deny_on(*_RULESETS)

    policy = reader.policy("main")

    assert policy.unavailable == (PolicySource.RULESETS,) and not policy.available
    assert policy.required_approvals == 2


def test_denied_classic_protection_leaves_the_policy_unavailable() -> None:
    host = FakePullRequestHost()
    host.rules["main"] = [pull_request_rule(1)]
    reader, gh = _read(host)
    gh.deny_on(*_PROTECTION)

    policy = reader.policy("main")

    assert policy.unavailable == (PolicySource.BRANCH_PROTECTION,) and policy.required_approvals == 1


def test_a_refusal_that_hides_the_branch_is_a_denial_not_an_absent_policy() -> None:
    reader, gh = _read(FakePullRequestHost())
    gh.fail_on(*_RULESETS, output="gh: Not Found (HTTP 404)")

    assert reader.policy("main").unavailable == (PolicySource.RULESETS,)


def test_both_sources_denied_name_both() -> None:
    reader, gh = _read(FakePullRequestHost())
    gh.deny_on(*_RULESETS)
    gh.deny_on(*_PROTECTION)

    assert reader.policy("main").unavailable == (PolicySource.RULESETS, PolicySource.BRANCH_PROTECTION)


def test_a_failure_that_is_not_a_refusal_is_raised() -> None:
    reader, gh = _read(FakePullRequestHost())
    gh.fail_on(*_PROTECTION, output="gh: Server Error (HTTP 500)")

    with pytest.raises(CommandError, match="500"):
        reader.policy("main")


def test_reading_the_policy_changes_nothing_on_github() -> None:
    host = FakePullRequestHost()
    host.rules["main"] = [pull_request_rule(1)]
    reader, gh = _read(host)

    reader.policy("main")

    assert gh.writes() == []

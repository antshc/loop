"""Babysit's fingerprints and agent requests against the goldens captured from the code that existed before the migration; the persisted-state golden is retired with the state model, and its saved states seed the adoption tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from software_factory.agents.roles.address import GROUP_FEEDBACK, RESOLVE_ISSUE
from software_factory.agents.roles.maintainer import DIAGNOSE_CI, FIX_CI
from software_factory.events.ci import BranchBroken
from software_factory.events.feedback import FeedbackBatch, GroupedIssue
from software_factory.events.pull_requests import NextAction, PullRequestPolled
from software_factory.events.specs import RepositoryTarget
from software_factory.shared.github.pull_requests.babysit import babysit_snapshot, bot_logins, ci_state
from software_factory.stages import ci, pull_requests
from software_factory.stages.address import GroupInput, IssueWork, group_feedback, resolve_issue
from factory_workflows.pr_babysit.policy import TRUST, fingerprint

from .golden import CLOSED, GOLDEN, GREEN, OPEN, build_host, pulls_over

SCENARIOS = [("open", OPEN), ("closed", CLOSED), ("green", GREEN)]
TARGET = RepositoryTarget("a/b", Path("/work"), Path("/trees"))


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text())


def polled_of(number: int) -> PullRequestPolled:
    """The golden pull request as the snapshot Stage reads it, for the acting account `me`."""
    read = pulls_over(build_host()).snapshot(number)
    return PullRequestPolled(TARGET, number, json.loads(json.dumps(babysit_snapshot(read))), "me", bot_logins(read))


def decided(polled: PullRequestPolled) -> NextAction:
    """What the definition's decide-next Stage makes of the pull request."""
    stage = pull_requests.decide_next(trust=TRUST, author=None, copilot=lambda _: False)
    return stage.executor.run(pull_requests.DecideInput(polled, (), ()))


@pytest.mark.parametrize("scenario,number", SCENARIOS)
def test_the_change_fingerprint_is_the_one_computed_before_the_migration(golden: dict, scenario: str, number: int) -> None:
    """Given each golden pull request, when the policy fingerprints it, then the value equals the one captured before the migration."""
    assert fingerprint(polled_of(number)) == golden[scenario]["fingerprint"]


@pytest.mark.parametrize("scenario,number", SCENARIOS)
def test_the_ci_signal_is_the_one_computed_before_the_migration(golden: dict, scenario: str, number: int) -> None:
    """Given each golden pull request, when its CI is read, then the pending, failed and green signals and the failed runs equal the golden."""
    state = ci_state(polled_of(number).snapshot)

    expected = golden[scenario]["ci"]
    assert {**{name: state[name] for name in ("pending", "failed", "green")}, "runs": [run["id"] for run in state["runs"]]} == expected


@pytest.mark.parametrize("scenario,number", SCENARIOS)
def test_the_address_requests_the_definition_makes_are_byte_identical_to_the_goldens(golden: dict, scenario: str, number: int) -> None:
    """Given each golden pull request, when the definition's decision selects its feedback and the address Stages build their requests, then both renderings equal the goldens."""
    polled = polled_of(number)
    action = decided(polled)
    batch = action if isinstance(action, FeedbackBatch) else FeedbackBatch(polled.head, False, ())

    group = group_feedback().executor.request(GroupInput(batch, ()), Path("/work"))
    resolve = resolve_issue(message=lambda _: "m", verify=lambda _: None).executor.request(
        IssueWork(GroupedIssue("all", None, batch.keys), batch, polled, (), ()), Path("/work"),
    )

    assert GROUP_FEEDBACK.encode(group) == golden[scenario]["envelopes"]["group"]
    assert RESOLVE_ISSUE.encode(resolve) == golden[scenario]["envelopes"]["resolve"]


class FailedLogs:
    """The account whose pull request client serves the failed logs the golden diagnose envelope carries."""

    def pull_requests(self, slug: str) -> FailedLogs:
        return self

    def failed_logs(self, run_ids) -> str:
        return "cart.py: duplicate product"


def test_the_maintainer_requests_are_byte_identical_to_the_goldens(golden: dict) -> None:
    """Given the golden pull request with failed CI, when the CI Stages build their requests, then the diagnose and fix renderings equal the goldens."""
    polled = polled_of(OPEN)

    diagnose = ci.diagnose().executor.request(ci.DiagnoseInput(polled, FailedLogs()), Path("/work"))
    fix = ci.fix(message=lambda _: "m", verify=lambda _: None).executor.request(
        ci.FixInput(polled, BranchBroken("CI evidence")), Path("/work"),
    )

    assert DIAGNOSE_CI.encode(diagnose) == golden["open"]["envelopes"]["diagnose"]
    assert FIX_CI.encode(fix) == golden["open"]["envelopes"]["fix"]

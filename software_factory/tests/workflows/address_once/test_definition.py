"""The address_once definition: it validates, reuses only existing Stages and the address fragment, and its wiring is the reviewed golden."""

from __future__ import annotations

from pathlib import Path

from software_factory.runtime import WorktreeGit
from software_factory.shared.agents.launcher import AgentLauncher
from software_factory.shared.git import GitClient
from software_factory.shared.github.account import GitHub
from software_factory.workflows.address_once import address_once, policy
from software_factory.workflows.pr_babysit import policy as babysit_policy

WIRING = Path(__file__).with_name("wiring.txt")


def definition():
    return address_once(lambda path: None)


def test_the_definition_is_valid() -> None:
    """Given the definition, when it is built, then every route, Event and workspace requirement resolves."""
    definition().check()


def test_the_wiring_table_equals_the_checked_in_golden() -> None:
    """Given the definition, when it explains itself, then which Stage feeds which equals the golden, so a definition change is a reviewable diff."""
    assert definition().explain() == WIRING.read_text()


def test_the_definition_needs_no_pacer_and_no_issue_tracker() -> None:
    """Given the definition, when its services are derived, then they are Git, the runtime's Git, GitHub and the agent launcher."""
    assert definition().services() == {GitClient, WorktreeGit, GitHub, AgentLauncher}


def test_the_feedback_trust_rules_are_the_ones_pr_babysit_applies() -> None:
    """Given both policies, when their trust rules are compared, then they are equal."""
    assert policy.TRUST == babysit_policy.TRUST

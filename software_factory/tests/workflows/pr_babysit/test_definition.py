"""The pr_babysit definition: it validates, its wiring is the reviewed golden, and its policy lives in values."""

from __future__ import annotations

from pathlib import Path

from software_factory.runtime import WorktreeGit
from software_factory.shared.agents.launcher import AgentLauncher
from software_factory.shared.git import GitClient
from software_factory.shared.github.account import GitHub
from software_factory.shared.pacing import Pacer
from software_factory.workflows.pr_babysit import pr_babysit
from software_factory.workflows.pr_babysit.settings import Repository, Settings

WIRING = Path(__file__).with_name("wiring.txt")


def definition():
    settings = Settings((Repository("a/b", Path("."), ("build",), ("test",)),), Path("."))
    return pr_babysit(settings, lambda path: None)


def test_the_definition_is_valid_for_default_settings() -> None:
    """Given default settings, when the definition is built, then every route, reroute, Event and workspace requirement resolves."""
    definition().check()


def test_the_wiring_table_equals_the_checked_in_golden() -> None:
    """Given default settings, when the definition explains itself, then which Stage feeds which equals the golden, so a definition change is a reviewable diff."""
    assert definition().explain() == WIRING.read_text()


def test_the_definition_needs_exactly_the_services_the_entry_point_builds() -> None:
    """Given the definition, when its services are derived, then they are Git, the runtime's Git, GitHub, the agent launcher and the pacer."""
    assert definition().services() == {GitClient, WorktreeGit, GitHub, AgentLauncher, Pacer}

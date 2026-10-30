"""The implement_spec definition: it validates, its wiring is the reviewed golden and its policy lives in values."""

from __future__ import annotations

from pathlib import Path

from software_factory.runtime import WorktreeGit
from software_factory.shared.agents.launcher import AgentLauncher
from software_factory.shared.git import GitClient
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.issues.client import Issues
from software_factory.workflows.implement_spec import ImplementSpecSettings, Targets, implement_spec

WIRING = Path(__file__).with_name("wiring.txt")


def test_the_definition_is_valid_for_default_settings() -> None:
    """Given default settings, when the definition is built, then every route, Event and workspace requirement resolves."""
    implement_spec(ImplementSpecSettings(), Targets(())).check()


def test_the_wiring_table_equals_the_checked_in_golden() -> None:
    """Given default settings, when the definition explains itself, then which Stage feeds which equals the golden, so a definition change is a reviewable diff."""
    assert implement_spec(ImplementSpecSettings(), Targets(())).explain() == WIRING.read_text()


def test_the_definition_needs_exactly_the_services_the_entry_point_builds() -> None:
    """Given the definition, when its services are derived, then they are the tracker, Git, the runtime's Git, GitHub and the agent launcher."""
    assert implement_spec(ImplementSpecSettings(), Targets(())).services() == {Issues, GitClient, WorktreeGit, GitHub, AgentLauncher}


def test_the_definition_has_no_verify_stage() -> None:
    """Given the ticket gate that trusts the developer's reported verification, when the wiring is read, then no verify Stage exists."""
    assert "verify" not in implement_spec(ImplementSpecSettings(), Targets(())).explain()


def test_the_finalization_reads_the_publication_made_after_delivery() -> None:
    """Given three publication placements, when the wiring is read, then finalize and the announcement read the one named publish-delivery."""
    wiring = implement_spec(ImplementSpecSettings(), Targets(())).explain()

    assert "published    event DraftPullRequestPublished from publish-delivery" in wiring
    assert wiring.count("from publish-delivery") == 2

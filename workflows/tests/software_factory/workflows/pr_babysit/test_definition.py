"""The pr_babysit definition: it validates, its wiring is the reviewed golden, and its policy lives in values."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from loop import AgentExecution, WorktreeGit
from loop.runtime.analysis import _stages_in
from loop.shared.git import GitClient

from workflows.software_factory.infrastructure.agents.launcher import AgentLauncher
from workflows.software_factory.infrastructure.github.account import GitHub
from workflows.software_factory.infrastructure.pacing import Pacer
from workflows.software_factory.workflows.pr_babysit.definition import pr_babysit
from workflows.software_factory.workflows.pr_babysit.settings import (
    Repository,
    Settings,
)

WIRING = Path(__file__).with_name("wiring.txt")


def definition():
    settings = Settings(
        (Repository("a/b", Path("."), ("build",), ("test",)),), Path(".")
    )
    return pr_babysit(settings, lambda path: None)


def test_the_definition_is_valid_for_default_settings() -> None:
    """Given default settings, when the definition is built, then every route, reroute, Event and workspace requirement resolves."""
    definition().check()


def test_the_configured_timeout_bounds_every_agent_stage() -> None:
    """Given a configured timeout, when the definition is built, then every agent Stage runs its role with that timeout."""
    settings = replace(
        Settings((Repository("a/b", Path("."), ("build",), ("test",)),), Path(".")),
        timeout=42,
    )

    agents = {
        stage.id: stage.executor.agent.profile.timeout_sec
        for stage in _stages_in(pr_babysit(settings, lambda path: None).nodes)
        if isinstance(stage.executor, AgentExecution)
    }

    assert set(agents) == {
        "diagnose-ci",
        "fix-ci",
        "group-feedback",
        "resolve-issue",
    } and set(agents.values()) == {42}


def test_the_wiring_table_equals_the_checked_in_golden() -> None:
    """Given default settings, when the definition explains itself, then which Stage feeds which equals the golden, so a definition change is a reviewable diff."""
    assert definition().explain() == WIRING.read_text()


def test_the_definition_needs_exactly_the_services_the_entry_point_builds() -> None:
    """Given the definition, when its services are derived, then they are Git, the runtime's Git, GitHub, the agent launcher and the pacer."""
    assert definition().services() == {
        GitClient,
        WorktreeGit,
        GitHub,
        AgentLauncher,
        Pacer,
    }

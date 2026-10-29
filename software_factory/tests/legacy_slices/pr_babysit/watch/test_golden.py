"""Babysit's fingerprints, persisted state, and agent envelopes against goldens captured from the code that read untyped dictionaries."""

from __future__ import annotations

import json

import pytest

from software_factory.legacy_slices.pr_babysit.model import PrState
from software_factory.legacy_slices.pr_babysit.watch.feedback import observe
from software_factory.legacy_slices.pr_babysit.watch.snapshot import babysit_snapshot

from .golden import GOLDEN, OPEN, build_host, capture_all, pulls_over


def _typed(pulls, number):
    return babysit_snapshot(pulls.snapshot(number))


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(GOLDEN.read_text())


@pytest.fixture
def captured(tmp_path) -> dict:
    return json.loads(json.dumps(capture_all(_typed, tmp_path)))


@pytest.mark.parametrize("scenario", ["open", "closed", "green"])
def test_the_change_fingerprint_is_the_one_computed_before_the_migration(golden, captured, scenario) -> None:
    assert captured[scenario]["fingerprint"] == golden[scenario]["fingerprint"]


@pytest.mark.parametrize("scenario", ["open", "closed", "green"])
def test_the_persisted_state_is_saved_in_the_same_format_and_version(golden, captured, scenario) -> None:
    saved = captured[scenario]["state_file"]

    assert saved == golden[scenario]["state_file"]
    assert json.loads(saved)["version"] == 1


@pytest.mark.parametrize("scenario", ["open", "closed", "green"])
@pytest.mark.parametrize("mode", ["group", "resolve", "diagnose", "fix"])
def test_the_agent_receives_the_input_envelope_it_received_before_the_migration(golden, captured, scenario, mode) -> None:
    assert captured[scenario]["envelopes"][mode] == golden[scenario]["envelopes"][mode]


@pytest.mark.parametrize("scenario", ["open", "closed", "green"])
def test_the_ci_signal_is_unchanged(golden, captured, scenario) -> None:
    assert captured[scenario]["ci"] == golden[scenario]["ci"]


def test_state_saved_before_the_migration_is_read_without_migration_and_written_back_unchanged(golden, tmp_path) -> None:
    saved = golden["open"]["state_file"]
    state = PrState(tmp_path, "a/b", OPEN)
    state.path.parent.mkdir(parents=True)
    state.path.write_text(saved)

    loaded = PrState(tmp_path, "a/b", OPEN)
    pending = observe(_typed(pulls_over(build_host()), OPEN), loaded, "me")

    assert loaded.data["version"] == 1
    assert [item["key"] for item in pending] == [key for key, item in loaded.data["items"].items() if item["status"] == "pending"]
    assert state.path.read_text() == saved

"""implement_spec's rendered prompts and a fake run's tracker writes against goldens captured before the Workflow code moved."""

from __future__ import annotations

from pathlib import Path

import pytest

from .golden import PROMPTS, SCENARIO, prompt_golden, record_tracker_writes, render, render_scenario


@pytest.mark.parametrize("name", PROMPTS)
def test_the_rendered_prompt_equals_the_golden_byte_for_byte(name: str) -> None:
    """Given fixed inputs, when a prompt is rendered, then it equals the captured golden."""
    assert render(name).encode() == prompt_golden(name).read_bytes()


def test_the_recorded_fake_run_writes_to_the_tracker_exactly_as_the_golden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given the fake agent, Git, GitHub and store, when the Workflow runs, then its tracker writes equal the golden."""
    code, writes = record_tracker_writes(monkeypatch, tmp_path)

    assert code == 0
    assert render_scenario(writes).encode() == SCENARIO.read_bytes()

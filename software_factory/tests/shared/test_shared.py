from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from dishka import Provider, Scope, make_container, provide

from software_factory.shared.agents.envelope import ContentDecodeError, LastJsonObject
from software_factory.shared.agents.prompts import PromptError, PromptLibrary, placeholders, render
from software_factory.shared.git import GitClient
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.repo import GhRunner
from software_factory.shared.process import CommandRunner, Runner
from software_factory.shared.providers import SharedProvider
from software_factory.shared.state import JsonFile, StateError, process_lock


def test_atomic_failure_preserves_previous_state(tmp_path) -> None:
    state = JsonFile(tmp_path / "state.json")
    state.write({"version": 1, "value": "before"})
    with patch("software_factory.shared.state.os.replace", side_effect=OSError("interrupted")):
        with pytest.raises(OSError):
            state.write({"version": 1, "value": "after"})
    assert state.read_versioned({})["value"] == "before"
    assert list(tmp_path.iterdir()) == [state.path]


def test_invalid_json_is_reported_and_not_reset(tmp_path) -> None:
    state = JsonFile(tmp_path / "state.json")
    state.path.write_text("{")

    with pytest.raises(StateError, match="Cannot read state"):
        state.read_versioned({"version": 1})
    assert state.path.read_text() == "{"


def test_a_wrong_schema_version_is_rejected(tmp_path) -> None:
    state = JsonFile(tmp_path / "state.json")
    state.write({"version": 2})

    with pytest.raises(StateError, match="Invalid state schema"):
        state.read_versioned({"version": 1})


def test_process_lock_excludes_a_duplicate_worker_and_releases(tmp_path) -> None:
    with process_lock(tmp_path / "worker.lock"):
        with pytest.raises(StateError):
            with process_lock(tmp_path / "worker.lock"):
                pytest.fail("duplicate worker acquired lock")
    with process_lock(tmp_path / "worker.lock"):
        pass


def test_prompts_render_every_placeholder_and_reject_a_missing_argument(tmp_path, caplog) -> None:
    (tmp_path / "task.md").write_text("Do {{ WHAT }} in {{WHERE}}")
    prompts = PromptLibrary(tmp_path)

    with caplog.at_level(logging.WARNING):
        assert prompts.render("task", {"WHAT": "x", "WHERE": "y", "EXTRA": "z"}) == "Do x in y"
    assert "unused prompt argument: EXTRA" in caplog.text
    assert placeholders(prompts.load("task")) == {"WHAT", "WHERE"}
    with pytest.raises(PromptError, match="missing prompt argument: WHERE"):
        render("{{WHERE}}", {})


def test_the_last_matching_json_object_wins_over_noise() -> None:
    decoder = LastJsonObject(lambda value: "status" in value)

    assert decoder.decode('noise {"a": 1} {"status": "x"} {broken {"status": "y"} tail') == {"status": "y"}
    with pytest.raises(ContentDecodeError):
        decoder.decode("no json")


def test_the_shared_provider_wires_git_and_github_over_one_runner_and_accepts_overrides() -> None:
    calls: list[tuple[str, ...]] = []

    class FakeRunner(Provider):
        scope = Scope.APP

        @provide(override=True)
        def runner(self) -> Runner:
            def run(args, *, cwd=None, timeout_s=None):
                calls.append(tuple(args))
                return '{"login": "me"}'
            return run

    real = make_container(SharedProvider(timeout_s=5))
    assert isinstance(real.get(Runner), CommandRunner) and isinstance(real.get(GitClient), GitClient)
    real.close()

    container = make_container(SharedProvider(), FakeRunner())
    assert container.get(GitHub).login() == "me"
    assert container.get(GhRunner)(("auth", "status")) and calls == [("gh", "api", "user"), ("gh", "auth", "status")]
    container.close()

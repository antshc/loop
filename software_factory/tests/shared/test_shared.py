from __future__ import annotations

from unittest.mock import patch

import pytest
from dishka import Provider, Scope, make_container, provide

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

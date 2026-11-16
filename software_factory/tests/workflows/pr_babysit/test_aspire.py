"""The Aspire dashboard is started only for a local OTLP endpoint nothing listens on."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from software_factory.workflows.pr_babysit import aspire


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_listening_endpoint_starts_no_dashboard(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        endpoint = f"http://127.0.0.1:{listener.getsockname()[1]}"

        assert aspire.ensure_dashboard(endpoint, tmp_path, 19000) is None


def test_an_unreachable_remote_endpoint_fails_the_launch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No OTLP collector"):
        aspire.ensure_dashboard("http://192.0.2.1:4318", tmp_path, 19000, wait=1)


def test_an_unreachable_local_endpoint_starts_the_dashboard_on_the_configured_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    port = _free_port()
    started: list[list[str]] = []

    class Process:
        pid = 1

        def poll(self) -> None:
            return None

    def popen(command: list[str], **_: Any) -> Process:
        started.append(command)
        return Process()

    probes = iter((None, None, f"http://localhost:{port}"))
    monkeypatch.setattr(aspire.shutil, "which", lambda _: "aspire")
    monkeypatch.setattr(aspire.subprocess, "Popen", popen)
    monkeypatch.setattr(aspire, "reachable_endpoint", lambda *_, **__: next(probes))
    monkeypatch.setattr(aspire.time, "sleep", lambda _: None)

    url = aspire.ensure_dashboard(f"http://localhost:{port}", tmp_path, 19000)

    assert url == "http://localhost:19000"
    [command] = started
    assert command[command.index("--frontend-url") + 1] == "http://localhost:19000"
    assert command[command.index("--otlp-http-url") + 1] == f"http://localhost:{port}"

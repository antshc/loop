"""Starts the shared Aspire dashboard that collects babysitters' telemetry when nothing listens on the configured OTLP endpoint."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from .log_export import SERVICE_NAME, reachable_endpoint

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def ensure_dashboard(
    endpoint: str | None, state_dir: Path, port: int, wait: float = 120
) -> str | None:
    """The dashboard URL once `endpoint` accepts OTLP: starts a detached `aspire dashboard run` for a local, unreachable endpoint; None without an endpoint or when one already listens."""
    if not endpoint or reachable_endpoint(endpoint, quiet=True):
        return None
    parts = urlsplit(endpoint)
    if parts.hostname not in _LOCAL_HOSTS:
        raise ValueError(
            f"No OTLP collector at {endpoint}; start it or set otel_endpoint to null"
        )
    aspire = shutil.which("aspire")
    if aspire is None:
        raise ValueError(
            f"No OTLP collector at {endpoint} and no `aspire` CLI to start the dashboard"
        )
    dashboard = f"http://localhost:{port}"
    log = state_dir / "aspire" / "dashboard.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [
        aspire,
        "dashboard",
        "run",
        "--non-interactive",
        "--nologo",
        "--frontend-url",
        dashboard,
        "--otlp-http-url",
        f"http://{parts.hostname}:{parts.port or 80}",
        "--allow-anonymous",
        "--application-name",
        SERVICE_NAME,
    ]
    with log.open("ab") as output:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            **_detached(),
        )
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ValueError(f"Aspire dashboard exited during startup; read {log}")
        if reachable_endpoint(endpoint, quiet=True):
            return dashboard
        time.sleep(0.5)
    raise ValueError(
        f"Aspire dashboard not ready after {wait:.0f}s; process {process.pid}, log {log}"
    )


def _detached() -> dict[str, object]:
    """Lets the dashboard outlive the launcher, so later babysitters share it."""
    if sys.platform == "win32":
        return {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        }
    return {"start_new_session": True}

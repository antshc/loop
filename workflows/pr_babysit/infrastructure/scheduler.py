from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from time import sleep

from .github import Github, remote_fingerprint
from ..settings import Settings
from .state import Blocked, PrState, Registry, process_lock

logger = logging.getLogger(__name__)


def child_command(config: Path, url: str) -> list[str]:
    """Launch an independent babysit workflow without a scheduler trace parent."""
    return [sys.executable, "-m", "workflows.pr_babysit", "--config", str(config), "--pr", url]


def child_environment() -> dict[str, str]:
    """Keep exporter configuration but discard inherited distributed trace context."""
    return {key: value for key, value in os.environ.items()
            if key.lower() not in {"traceparent", "tracestate", "baggage"}}


def serve(settings: Settings, config: Path, *, include_existing=False, retry: str | None = None,
          github=None, spawn=subprocess.Popen, wait=sleep, stop=lambda: False) -> int:
    """Scan continuously while child processes own independent babysit executions."""
    github = github or Github()
    author = settings.author or github.login()
    children = {}
    with process_lock(settings.state_dir / "scheduler.lock"):
        registry = Registry(settings.state_dir, author, [repo.name for repo in settings.repositories])
        try:
            while not stop():
                for key, child in list(children.items()):
                    if child.poll() is None:
                        continue
                    del children[key]
                    record = registry.data["prs"][key]
                    state = PrState(settings.state_dir, record["repo"], record["number"])
                    if state.data["status"] in {"running", "queued"}:
                        state.status("blocked", reason=f"Worker exited {child.returncode}; explicit retry required",
                                     fingerprint="worker-crash")
                try:
                    prs = discover(github, settings.repositories, author)
                    registry.enroll(prs, include_existing)
                    open_keys = {f"{repo}#{pr['number']}" for repo, pr in prs}
                    for key, record in registry.data["prs"].items():
                        if key in children:
                            continue
                        state = PrState(settings.state_dir, record["repo"], record["number"])
                        status = state.data["status"]
                        if status == "ignored":
                            continue
                        if key not in open_keys:
                            if status != "completed":
                                current = github.snapshot(record["repo"], record["number"])
                                if current["pr"]["state"] != "open":
                                    state.status("completed")
                            continue
                        explicit_retry = retry == record["url"]
                        if status == "blocked" and not explicit_retry:
                            snapshot = github.snapshot(record["repo"], record["number"])
                            if (state.data.get("fingerprint") == "worker-crash" or
                                    remote_fingerprint(snapshot) == state.data.get("fingerprint")):
                                continue
                        if len(children) >= settings.concurrency:
                            continue
                        # A surviving child from an interrupted scheduler still owns this PR lock.
                        try:
                            with process_lock(state.path.with_suffix(".lock")):
                                state.status("queued")
                        except Blocked:
                            continue
                        logger.info("Starting babysit for %s", key)
                        children[key] = spawn(child_command(config, record["url"]), env=child_environment())
                        if explicit_retry:
                            retry = None
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exception:
                    logger.warning("PR discovery unavailable: %s", exception)
                wait(settings.interval)
        finally:
            for child in children.values():
                if child.poll() is None:
                    child.terminate()
            for child in children.values():
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
    return 0


def discover(github, repositories, author: str) -> list[tuple[str, dict]]:
    """Return a complete discovery scan before advancing the startup baseline."""
    return [(repo.name, pr) for repo in repositories for pr in github.open_prs(repo.name, author)]

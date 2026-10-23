from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from time import sleep

from ..pr_maintenance.github import Github, remote_fingerprint
from ..pr_maintenance.settings import Settings
from ..pr_maintenance.state import Blocked, PrState, process_lock
from .discovery import discover
from .registry import Registry

logger = logging.getLogger(__name__)


def child_command(config: Path, url: str) -> list[str]:
    """Launch an independent babysit workflow without a scheduler trace parent."""
    return [sys.executable, "-m", "workflows.babysit", "--config", str(config), "--pr", url]


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


def main(argv=None) -> int:
    """Start the scheduler with local logging and no telemetry provider."""
    parser = argparse.ArgumentParser(description="Continuously discover and babysit user-authored PRs")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--retry", help="Explicitly retry one enrolled blocked PR URL")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    stopping = False

    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        return serve(Settings.load(args.config), args.config.resolve(), include_existing=args.include_existing,
                     retry=args.retry, stop=lambda: stopping)
    except (Blocked, ValueError, OSError) as exception:
        logger.error("Scheduler stopped: %s", exception)
        return 1

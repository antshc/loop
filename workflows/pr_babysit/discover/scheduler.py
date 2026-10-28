"""Continuous discovery: scan, enroll, and supervise per-PR maintenance processes."""

from __future__ import annotations

import logging
import subprocess

from workflows.shared.github.account import GitHub
from workflows.shared.github.pull_requests.model import PullRequest

from ..model import Blocked, DiscoveryRequest, Pacer, PrState, StopSignal, exclusive
from ..settings import Settings
from ..watch.feedback import fingerprint
from ..watch.snapshot import babysit_snapshot
from .children import Child, ChildLauncher
from .registry import Registry

logger = logging.getLogger(__name__)


class Scheduler:
    """Scans continuously while child processes own independent babysit executions."""

    def __init__(self, settings: Settings, request: DiscoveryRequest, github: GitHub,
                 children: ChildLauncher, pacer: Pacer, stop: StopSignal) -> None:
        self._settings = settings
        self._request = request
        self._github = github
        self._launcher = children
        self._pacer = pacer
        self._stop = stop

    def discover(self, author: str) -> list[tuple[str, PullRequest]]:
        """A complete discovery scan, taken before the startup baseline advances."""
        return [(repo.name, pr) for repo in self._settings.repositories
                for pr in self._github.pull_requests(repo.name).open_by(author)]

    def serve(self) -> int:
        settings, github = self._settings, self._github
        retry = self._request.retry
        author = settings.author or github.login()
        children: dict[str, Child] = {}
        with exclusive(settings.state_dir / "scheduler.lock"):
            registry = Registry(settings.state_dir, author, [repo.name for repo in settings.repositories])
            try:
                while not self._stop.requested():
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
                        prs = self.discover(author)
                        registry.enroll(prs, self._request.include_existing)
                        open_keys = {f"{repo}#{pr.number}" for repo, pr in prs}
                        for key, record in registry.data["prs"].items():
                            if key in children:
                                continue
                            state = PrState(settings.state_dir, record["repo"], record["number"])
                            status = state.data["status"]
                            if status == "ignored":
                                continue
                            pulls = github.pull_requests(record["repo"])
                            if key not in open_keys:
                                if status != "completed":
                                    if not pulls.pull_request(record["number"]).is_open:
                                        state.status("completed")
                                continue
                            explicit_retry = retry == record["url"]
                            if status == "blocked" and not explicit_retry:
                                if (state.data.get("fingerprint") == "worker-crash" or
                                        fingerprint(babysit_snapshot(pulls.snapshot(record["number"])))
                                        == state.data.get("fingerprint")):
                                    continue
                            if len(children) >= settings.concurrency:
                                continue
                            # A surviving child from an interrupted scheduler still owns this PR lock.
                            try:
                                with exclusive(state.path.with_suffix(".lock")):
                                    state.status("queued")
                            except Blocked:
                                continue
                            logger.info("Starting babysit for %s", key)
                            children[key] = self._launcher.start(self._request.config, record["url"])
                            if explicit_retry:
                                retry = None
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exception:
                        logger.warning("PR discovery unavailable: %s", exception)
                    self._pacer.wait(settings.interval)
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

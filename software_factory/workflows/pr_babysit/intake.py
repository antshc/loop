"""pr_babysit's intake: which pull requests a run takes up, scanned continuously, one child process each, as the Execution ledger and the discovery baseline say."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from software_factory.events.pull_requests import PullRequestWork
from software_factory.events.specs import RepositoryTarget
from software_factory.runtime import Ledger, LedgerCorrupted, RunStatus, StopSignal, WorkItemLocked
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.pull_requests.model import PullRequest
from software_factory.shared.pacing import Pacer
from software_factory.shared.state import JsonFile, StateError, process_lock
from software_factory.stages import pull_requests
from software_factory.stages.ci import DiagnoseInput, signal

from . import NAME
from .legacy import adopt_v1, read_status, state_path
from .policy import ADOPTED, Blocked, fingerprint, has_changed, is_blocked, released_for_change
from .settings import Settings

logger = logging.getLogger("workflow.pr_babysit")

# What a pull request work item is about: itself, which does not change while it is maintained.
REVISION = "pull-request"

# The statuses a worker that finished cleanly leaves its run in.
_CLOSED = {"completed", "idle", "held", "handed_off", "interrupted"}


@dataclass(frozen=True)
class DiscoveryRequest:
    """How the discovery scheduler was asked to run."""

    config: Path
    include_existing: bool = False
    retry: str | None = None


class Child(Protocol):
    """The subset of `subprocess.Popen` the scheduler supervises."""

    returncode: int | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class ChildLauncher(Protocol):
    """Starts the maintenance process of one pull request."""

    def start(self, config: Path, url: str) -> Child: ...


def child_command(config: Path, url: str) -> list[str]:
    """An independent babysit process without a scheduler trace parent."""
    return [sys.executable, "-m", "factory_workflows.pr_babysit", "--config", str(config), "--pr", url]


def child_environment() -> dict[str, str]:
    """Keeps exporter configuration but discards inherited distributed trace context."""
    return {key: value for key, value in os.environ.items() if key.lower() not in {"traceparent", "tracestate", "baggage"}}


class ProcessLauncher:
    """Starts `python -m factory_workflows.pr_babysit --pr <url>` as a child process."""

    def start(self, config: Path, url: str) -> Child:
        """The running child."""
        return subprocess.Popen(child_command(config, url), env=child_environment())


def work_of(settings: Settings, slug: str, number: int) -> PullRequestWork:
    """The work item of one configured repository's pull request; its recovery worktrees live under the configured worktrees folder."""
    repository = settings.repository(slug)
    return PullRequestWork(RepositoryTarget(slug, repository.path, settings.worktrees_root), number, REVISION)


def adopt_state(settings: Settings, ledger: Ledger, github: GitHub, work: PullRequestWork) -> bool:
    """Adopts the earlier version's state of the pull request into the ledger when it has not been; True when it was adopted now."""
    def ci_signal() -> str:
        return signal(DiagnoseInput(pull_requests.read(work, github), github))

    adopted = adopt_v1(state_path(settings.state_dir, work.target.slug, work.number), ledger, work, ci_signal=ci_signal)
    if adopted:
        logger.info("%s: adopted the per-PR state of the earlier version", work.id)
    return adopted


def release_if_changed(ledger: Ledger, github: GitHub, work: PullRequestWork) -> bool:
    """Releases blocked work whose pull request changed since it was blocked, so its next run starts afresh; True when released."""
    status = ledger.status(NAME, work.id)
    if not is_blocked(status) or not released_for_change(status, fingerprint(pull_requests.read(work, github))):
        return False
    ledger.release(NAME, work.id, "the pull request changed since it was blocked")
    return True


class Registry:
    """The discovery baseline: which pull requests are enrolled, each exactly once, and which of them are ignored or crashed a worker."""

    def __init__(self, root: Path, author: str, repositories: list[str]) -> None:
        self.root, self.author, self.repositories = root, author, sorted(repositories)
        self.path = root / "scheduler.json"
        self._file = JsonFile(self.path)
        self._data = self._read({"version": 1, "initialized": False, "prs": {}, "author": author, "repositories": self.repositories})
        if not isinstance(self._data.get("prs"), dict):
            raise Blocked(f"Invalid scheduler registry: {self.path}")
        if self._data.get("author") != author or self._data.get("repositories") != self.repositories:
            raise Blocked("Discovery configuration changed; choose a new state_dir or migrate its baseline")
        self._adopt_ignored()

    @property
    def prs(self) -> dict[str, dict]:
        """The enrolled pull requests by `owner/repo#number`."""
        return self._data["prs"]

    def enroll(self, prs: list[tuple[str, PullRequest]], include_existing: bool) -> None:
        """Commits a complete baseline, then durably enrolls future discoveries."""
        for repo, pr in prs:
            key = f"{repo}#{pr.number}"
            if key not in self.prs:
                ignored = not (self._data["initialized"] or include_existing)
                self.prs[key] = {"repo": repo, "number": pr.number, "url": pr.url, "ignored": ignored}
        self._data["initialized"] = True
        self._file.write(self._data)

    def mark(self, key: str, *, crashed: bool) -> None:
        """Remembers that the pull request's worker died, or that a person asked to retry it."""
        self.prs[key]["crashed"] = crashed
        self._file.write(self._data)

    def _adopt_ignored(self) -> None:
        """A pull request the earlier version's baseline ignored stays ignored."""
        unmarked = [record for record in self.prs.values() if "ignored" not in record]
        for record in unmarked:
            record["ignored"] = read_status(state_path(self.root, record["repo"], record["number"])) == "ignored"
        if unmarked:
            self._file.write(self._data)

    def _read(self, default: dict) -> dict:
        try:
            return self._file.read_versioned(default)
        except StateError as exception:
            raise Blocked(str(exception)) from exception


class Scheduler:
    """Scans continuously while child processes own independent babysit runs, one per pull request."""

    def __init__(
        self, settings: Settings, request: DiscoveryRequest, github: GitHub, ledger: Ledger, children: ChildLauncher,
        pacer: Pacer, stop: StopSignal,
    ) -> None:
        self._settings = settings
        self._request = request
        self._github = github
        self._ledger = ledger
        self._launcher = children
        self._pacer = pacer
        self._stop = stop

    def discover(self, author: str) -> list[tuple[str, PullRequest]]:
        """A complete discovery scan, taken before the startup baseline advances."""
        return [
            (repository.name, pr) for repository in self._settings.repositories
            for pr in self._github.pull_requests(repository.name).open_by(author)
        ]

    def serve(self) -> int:
        """Scans until asked to stop, then stops the children; always 0."""
        settings = self._settings
        author = settings.author or self._github.login()
        retry = self._request.retry
        children: dict[str, Child] = {}
        try:
            with process_lock(settings.state_dir / "scheduler.lock"):
                registry = Registry(settings.state_dir, author, [repository.name for repository in settings.repositories])
                try:
                    while not self._stop.requested():
                        self._reap(children, registry)
                        retry = self._scan(author, registry, children, retry)
                        self._pacer.wait(settings.interval)
                finally:
                    self._stop_children(children)
        except StateError as exception:
            raise Blocked(str(exception)) from exception
        return 0

    def _reap(self, children: dict[str, Child], registry: Registry) -> None:
        """Forgets the children that exited; a worker that died without closing its run needs an explicit retry."""
        for key, child in list(children.items()):
            if child.poll() is None:
                continue
            del children[key]
            record = registry.prs[key]
            try:
                status = self._ledger.status(NAME, f"{record['repo']}#{record['number']}")
            except LedgerCorrupted as exception:
                self._quarantine(key, registry, exception)
                continue
            if status is None or status.status not in _CLOSED:
                logger.warning("%s: worker exited %s without closing its run; explicit retry required", key, child.returncode)
                registry.mark(key, crashed=True)

    def _scan(self, author: str, registry: Registry, children: dict[str, Child], retry: str | None) -> str | None:
        """One pass: enrol what is new, start a child for each pull request that may run; returns the retry still to apply."""
        try:
            prs = self.discover(author)
            registry.enroll(prs, self._request.include_existing)
            open_keys = {f"{repo}#{pr.number}" for repo, pr in prs}
            for key, record in list(registry.prs.items()):
                if key in children or record.get("ignored") or key not in open_keys:
                    continue
                explicit = retry == record["url"]
                try:
                    starts = self._starts(key, record, registry, explicit)
                except LedgerCorrupted as exception:
                    self._quarantine(key, registry, exception)
                    continue
                if not starts or len(children) >= self._settings.concurrency:
                    continue
                logger.info("Starting babysit for %s", key)
                children[key] = self._launcher.start(self._request.config, record["url"])
                retry = None if explicit else retry
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError) as exception:
            logger.warning("PR discovery unavailable: %s", exception)
        return retry

    def _starts(self, key: str, record: dict, registry: Registry, explicit: bool) -> bool:
        """Whether the pull request's worker should start now: not held for a person unless retried or changed, and not still owned by a surviving worker."""
        work = work_of(self._settings, record["repo"], record["number"])
        try:
            adopt_state(self._settings, self._ledger, self._github, work)
        except LedgerCorrupted:
            raise
        except ValueError as exception:
            logger.warning("%s: %s", key, exception)
            return False
        if record.get("crashed"):
            if not explicit:
                return False
            registry.mark(key, crashed=False)
        status = self._ledger.status(NAME, work.id)
        if is_blocked(status) and not self._unblocked(work, status, explicit):
            return False
        return self._free(work)

    def _unblocked(self, work: PullRequestWork, status: RunStatus, explicit: bool) -> bool:
        """Whether blocked work runs again: a retry releases it, a pull request that changed since is released unless it resumes work adopted from the earlier version."""
        if explicit:
            self._ledger.release(NAME, work.id, "retry requested")
            return True
        if not has_changed(status, fingerprint(pull_requests.read(work, self._github))):
            return False
        if not status.attributes.get(ADOPTED):
            self._ledger.release(NAME, work.id, "the pull request changed since it was blocked")
        return True

    def _quarantine(self, key: str, registry: Registry, exception: LedgerCorrupted) -> None:
        """Marks one pull request crashed for a damaged log, so it needs a person and the others go on."""
        logger.error("%s: %s; repair or move the log, then retry", key, exception)
        registry.mark(key, crashed=True)

    def _free(self, work: PullRequestWork) -> bool:
        """False while a surviving worker of an interrupted scheduler still owns the pull request."""
        try:
            with self._ledger.item(NAME, work.id).lock():
                return True
        except WorkItemLocked:
            return False

    def _stop_children(self, children: dict[str, Child]) -> None:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
        for child in children.values():
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=10)

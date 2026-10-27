"""The continuous maintenance loop of one PR."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from uuid import uuid4

from loop import WorktreeError

from workflows.shared.git import GitClient
from workflows.shared.github.account import GitHub
from workflows.shared.github.pull_requests import PullRequests
from workflows.shared.telemetry import Telemetry

from ..address.reviews import ReviewAddresser
from ..maintain_ci.failures import CiMaintainer
from ..model import BaselineFailed, Blocked, Pacer, PrState, PrTarget, StopSignal, exclusive
from ..settings import Repository, Settings
from ..watch.feedback import ci_state, fingerprint, observe
from .definition import babysit_workflow

logger = logging.getLogger(__name__)

_COPILOT = {"copilot", "copilot-pull-request-reviewer"}


class CheckoutGuard:
    """Refuses a checkout whose origin is not the PR's repository."""

    def __init__(self, git: GitClient) -> None:
        self._git = git

    def validate(self, path: Path, repo: str) -> None:
        if self._git.origin_slug(path).lower() != repo.lower():
            raise Blocked(f"Origin does not match {repo}: {path}")


class PrMonitor:
    """Polls one PR, dispatching review and CI work; quiet polls never create telemetry phases."""

    def __init__(self, settings: Settings, target: PrTarget, repository: Repository, state: PrState,
                 github: GitHub, pulls: PullRequests, guard: CheckoutGuard, addresser: ReviewAddresser,
                 maintainer: CiMaintainer, telemetry: Telemetry, pacer: Pacer, stop: StopSignal) -> None:
        self._settings = settings
        self._target = target
        self._repository = repository
        self._state = state
        self._github = github
        self._pulls = pulls
        self._guard = guard
        self._addresser = addresser
        self._maintainer = maintainer
        self._telemetry = telemetry
        self._pacer = pacer
        self._stop = stop

    def run(self) -> int:
        """0 once the PR closes, 1 when it blocks, 130 when interrupted."""
        settings, state, pulls = self._settings, self._state, self._pulls
        repo, number = self._target.repo, self._target.number
        with exclusive(state.path.with_suffix(".lock")):
            login = self._github.login()
            self._guard.validate(self._repository.path, repo)
            execution = uuid4().hex
            workflow = babysit_workflow(observer=self._telemetry.observer)
            with workflow.run(repository=repo, pr=number, execution=execution) as flow:
                state.status("running", execution=execution, reason="")
                ready_before = None
                snapshot = None
                try:
                    while not self._stop.requested():
                        snapshot = pulls.snapshot(number)
                        pr = snapshot["pr"]
                        if pr["state"] != "open":
                            state.status("completed", head=pr["head"]["sha"],
                                         outcome="merged" if pr.get("merged") else "closed")
                            flow.set(outcome=state.data["outcome"])
                            return 0
                        if (pr["user"]["login"].lower() != (settings.author or login).lower()
                                or (pr["head"].get("repo") or {}).get("full_name", "").lower() != repo.lower()):
                            raise Blocked("PR author changed or fork head is not supported by this checkout")
                        flow.set(head=pr["head"]["sha"])
                        state.data["head"] = pr["head"]["sha"]
                        items = observe(snapshot, state, login)
                        batch = state.data.get("batch")
                        if (batch and batch["kind"] == "address") or (not batch and items):
                            try:
                                self._addresser.address(snapshot, items, login, flow)
                            except BaselineFailed:
                                if not ci_state(snapshot)["failed"]:
                                    raise
                                self._maintainer.maintain(snapshot, login, flow)
                                self._pacer.wait(settings.interval)
                            continue
                        if (batch and batch["kind"] == "ci") or ci_state(snapshot)["failed"]:
                            self._maintainer.maintain(snapshot, login, flow)
                            self._pacer.wait(settings.interval)
                            continue
                        sha = pr["head"]["sha"]
                        if settings.copilot_review and sha not in state.data["copilot"]:
                            try:
                                pulls.request_copilot(number)
                                state.data["copilot"][sha] = "requested"
                            except subprocess.CalledProcessError:
                                state.data["copilot"][sha] = "unavailable"
                            state.save()
                            continue
                        copilot_pending = any(user["login"].lower().removesuffix("[bot]") in _COPILOT
                                              for user in pr.get("requested_reviewers", []))
                        ready = (ci_state(snapshot)["green"] and not copilot_pending and not pr["draft"]
                                 and pr.get("mergeable") is True and pr.get("mergeable_state") == "clean"
                                 and snapshot.get("review", {}).get("reviewDecision")
                                 not in {"REVIEW_REQUIRED", "CHANGES_REQUESTED"})
                        if ready != ready_before:
                            with flow.phase("ready") as phase:
                                phase.set(ready=ready, head=sha)
                            ready_before = ready
                        self._pacer.wait(settings.interval)
                    state.status("interrupted")
                    flow.set(outcome="interrupted")
                    return 130
                except (Blocked, WorktreeError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
                        OSError, ValueError) as exception:
                    reason = str(exception)
                    # Reconcile after our own replies so they cannot immediately retrigger a blocked PR.
                    try:
                        blocked_on = fingerprint(pulls.snapshot(number))
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError):
                        blocked_on = fingerprint(snapshot) if snapshot is not None else ""
                    state.status("blocked", reason=reason, fingerprint=blocked_on)
                    flow.fail(reason)
                    logger.error("%s#%s blocked: %s", repo, number, reason)
                    return 1
                except KeyboardInterrupt:
                    state.status("interrupted")
                    raise

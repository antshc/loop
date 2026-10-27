from __future__ import annotations

import logging
import subprocess
from time import sleep
from uuid import uuid4

from loop import Agent, WorktreeError, otlp_observer

from ..infrastructure.git import Git
from ..infrastructure.github import Github
from ..infrastructure.state import Blocked, PrState, process_lock
from ..infrastructure.verification import Verification
from ..settings import Settings
from .address import BaselineFailed, address_reviews
from .maintainer import maintain_ci
from .definition import babysit_workflow
from .watcher import ci_state, fingerprint, observe

logger = logging.getLogger(__name__)


def run(settings: Settings, repo: str, number: int, *, github=None, git=None, observer=None,
        new_agent=Agent, wait=sleep, stop=lambda: False, verification=None) -> int:
    """Monitor one PR continuously; quiet polls never create telemetry phases."""
    github, git = github or Github(), git or Git()
    verify = verification or Verification()
    repository = settings.repository(repo)
    state = PrState(settings.state_dir, repo, number)
    with process_lock(state.path.with_suffix(".lock")):
        login = github.login()
        git.validate(repository.path, repo)
        if observer is None and settings.otel_endpoint:
            observer = otlp_observer(endpoint=settings.otel_endpoint, service_name="loop-babysit")
        execution = uuid4().hex
        with babysit_workflow(observer=observer).run(repository=repo, pr=number, execution=execution) as flow:
            state.status("running", execution=execution, reason="")
            ready_before = None
            try:
                while not stop():
                    snapshot = github.snapshot(repo, number)
                    pr = snapshot["pr"]
                    if pr["state"] != "open":
                        state.status("completed", head=pr["head"]["sha"], outcome="merged" if pr.get("merged") else "closed")
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
                            address_reviews(settings, repository, snapshot, items, state, github, git, login, flow, observer, new_agent, verify)
                        except BaselineFailed:
                            if not ci_state(snapshot)["failed"]:
                                raise
                            maintain_ci(settings, repository, snapshot, state, github, git, login, flow, observer, new_agent, verify)
                            wait(settings.interval)
                        continue
                    if (batch and batch["kind"] == "ci") or ci_state(snapshot)["failed"]:
                        maintain_ci(settings, repository, snapshot, state, github, git, login, flow, observer, new_agent, verify)
                        wait(settings.interval)
                        continue
                    sha = pr["head"]["sha"]
                    if settings.copilot_review and sha not in state.data["copilot"]:
                        try:
                            github.request_copilot(repo, number)
                            state.data["copilot"][sha] = "requested"
                        except subprocess.CalledProcessError:
                            state.data["copilot"][sha] = "unavailable"
                        state.save()
                        continue
                    copilot_pending = any(user["login"].lower().removesuffix("[bot]") in
                        {"copilot", "copilot-pull-request-reviewer"} for user in pr.get("requested_reviewers", []))
                    ready = (ci_state(snapshot)["green"] and not copilot_pending and not pr["draft"]
                             and pr.get("mergeable") is True and pr.get("mergeable_state") == "clean"
                             and snapshot.get("review", {}).get("reviewDecision") not in {"REVIEW_REQUIRED", "CHANGES_REQUESTED"})
                    if ready != ready_before:
                        with flow.phase("ready") as phase:
                            phase.set(ready=ready, head=sha)
                        ready_before = ready
                    wait(settings.interval)
                state.status("interrupted")
                flow.set(outcome="interrupted")
                return 130
            except (Blocked, WorktreeError, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError) as exception:
                reason = str(exception)
                # Reconcile after our own replies so they cannot immediately retrigger a blocked PR.
                try:
                    blocked_on = fingerprint(github.snapshot(repo, number))
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError):
                    blocked_on = fingerprint(snapshot) if "snapshot" in locals() else ""
                state.status("blocked", reason=reason, fingerprint=blocked_on)
                flow.fail(reason)
                logger.error("%s#%s blocked: %s", repo, number, reason)
                return 1
            except KeyboardInterrupt:
                state.status("interrupted")
                raise

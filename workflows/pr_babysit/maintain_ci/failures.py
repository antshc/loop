"""Diagnoses CI failures before choosing a Python-published fix or a bounded rerun."""

from __future__ import annotations

from dataclasses import replace

from loop import WorkflowRun

from workflows.shared.github.pull_requests import PullRequests

from ..agents import MAINTAINER
from ..agents.ask import ask
from ..agents.workspace import PrWorkspace
from ..model import Blocked, PrState
from ..publish.changes import ChangePublisher
from ..settings import Settings
from ..watch.feedback import ci_state, digest


class CiMaintainer:
    """Spends the per-head retry budget only on transient failures; fixes branch failures."""

    def __init__(self, settings: Settings, state: PrState, pulls: PullRequests,
                 workspace: PrWorkspace, changes: ChangePublisher) -> None:
        self._settings = settings
        self._profile = replace(MAINTAINER, timeout_sec=settings.timeout)
        self._state = state
        self._pulls = pulls
        self._workspace = workspace
        self._changes = changes

    def maintain(self, snapshot: dict, login: str, flow: WorkflowRun) -> None:
        state, changes, pulls = self._state, self._changes, self._pulls
        ci = ci_state(snapshot)
        retry_signal = digest([(run["id"], run.get("run_attempt", 1)) for run in ci["runs"]])
        if (not state.data.get("batch")
                and state.data.get("retry_wait", {}).get(snapshot["pr"]["head"]["sha"]) == retry_signal):
            return
        with self._workspace.open(snapshot) as workspace:
            path = workspace.path
            if state.data["batch"]:
                changes.recover(path)
                if state.data["batch"]["index"]:
                    changes.publish(path, login, flow)
                    return
            if not changes.clean(path):
                raise Blocked("Worktree contains unverified edits")
            head = changes.head(path)
            logs = pulls.failed_logs(ci["runs"]) if ci["runs"] else "No Actions run logs; inspect check/status links."
            with flow.phase("diagnose"):
                diagnosis = ask(workspace, self._profile, "diagnose", {"snapshot": snapshot, "logs": logs})
            changes.read_only(path, head)
            classification = diagnosis.get("classification")
            if classification == "transient":
                if ci["pending"]:
                    return
                used = state.data["retries"].get(head, 0)
                if used >= self._settings.retries or not ci["runs"]:
                    raise Blocked("Transient CI retry budget exhausted or no rerunnable runs")
                # Reserve the cycle before side effects, so a crash cannot exceed the budget.
                state.data["retries"][head] = used + 1
                state.data.setdefault("retry_wait", {})[head] = retry_signal
                state.save()
                with flow.phase("publish", action="retry"):
                    for run in ci["runs"]:
                        pulls.rerun_failed(run["id"])
                return
            if classification != "branch":
                raise Blocked(diagnosis.get("reason") or "CI diagnosis is unclear")
            if not state.data["batch"]:
                state.data["batch"] = {"kind": "ci", "base": head, "head": head,
                    "branch": snapshot["pr"]["head"]["ref"], "index": 0, "replies": {}, "inflight": None}
                state.save()
            with flow.phase("fix-ci"):
                result = ask(workspace, self._profile, "fix", {"diagnosis": diagnosis, "snapshot": snapshot})
            changes.agent_head(path, head)
            if result.get("verdict") != "fixed":
                raise Blocked(result.get("reason") or "Maintainer could not fix CI")
            changes.commit_issue(path, f"babysit: fix CI (PR #{snapshot['pr']['number']})", {}, flow)
            changes.publish(path, login, flow)

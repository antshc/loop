from __future__ import annotations

from dataclasses import replace

from ..agents import MAINTAINER
from ..infrastructure.github import digest
from ..infrastructure.state import Blocked
from .address import ask
from .changes import _agent_head, _read_only, _recover_commit, _commit_issue, _publish
from .watcher import ci_state
from .workspace import open_workspace


def maintain_ci(settings, repository, snapshot, state, github, git, login, flow, observer, new_agent, verify) -> None:
    """Diagnose failures before choosing Python-owned publication or bounded retries."""
    maintainer = replace(MAINTAINER, timeout_sec=settings.timeout)
    ci = ci_state(snapshot)
    retry_signal = digest([(run["id"], run.get("run_attempt", 1)) for run in ci["runs"]])
    if not state.data.get("batch") and state.data.get("retry_wait", {}).get(snapshot["pr"]["head"]["sha"]) == retry_signal:
        return
    with open_workspace(settings, repository, snapshot, state, git, observer, new_agent) as workspace:
        path = workspace.path
        if state.data["batch"]:
            _recover_commit(state, git, path)
            if state.data["batch"]["index"]:
                _publish(state, git, path, github, repository.name, snapshot["pr"]["number"], login, flow)
                return
        if not git.clean(path):
            raise Blocked("Worktree contains unverified edits")
        head = git.head(path)
        logs = github.logs(repository.name, ci["runs"]) if ci["runs"] else "No Actions run logs; inspect check/status links."
        with flow.phase("diagnose"):
            diagnosis = ask(workspace, maintainer, "diagnose", {"snapshot": snapshot, "logs": logs})
        _read_only(git, path, head)
        classification = diagnosis.get("classification")
        if classification == "transient":
            if ci["pending"]:
                return
            used = state.data["retries"].get(head, 0)
            if used >= settings.retries or not ci["runs"]:
                raise Blocked("Transient CI retry budget exhausted or no rerunnable runs")
            # Reserve the cycle before side effects, so a crash cannot exceed the budget.
            state.data["retries"][head] = used + 1
            state.data.setdefault("retry_wait", {})[head] = retry_signal
            state.save()
            with flow.phase("publish", action="retry"):
                for run in ci["runs"]:
                    github.retry(repository.name, run["id"])
            return
        if classification != "branch":
            raise Blocked(diagnosis.get("reason") or "CI diagnosis is unclear")
        if not state.data["batch"]:
            state.data["batch"] = {"kind": "ci", "base": head, "head": head,
                "branch": snapshot["pr"]["head"]["ref"], "index": 0, "replies": {}, "inflight": None}
            state.save()
        with flow.phase("fix-ci"):
            result = ask(workspace, maintainer, "fix", {"diagnosis": diagnosis, "snapshot": snapshot})
        _agent_head(git, path, head)
        if result.get("verdict") != "fixed":
            raise Blocked(result.get("reason") or "Maintainer could not fix CI")
        _commit_issue(state, git, path, f"babysit: fix CI (PR #{snapshot['pr']['number']})", {},
                      flow, repository, settings.timeout, verify)
        _publish(state, git, path, github, repository.name, snapshot["pr"]["number"], login, flow)

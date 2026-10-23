from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from pathlib import Path
from time import sleep
from uuid import uuid4

from loop import Agent, GitOptions, otlp_observer

from .address import ask, group_issues, validate_resolution
from .agents import ADDRESS, MAINTAINER
from .definition import babysit_workflow
from .git import Git, PrWorktree
from ..pr_maintenance.github import Github, digest
from ..pr_maintenance.settings import Settings
from ..pr_maintenance.state import Blocked, PrState, process_lock
from .watcher import ci_state, fingerprint, observe

logger = logging.getLogger(__name__)


class BaselineFailed(Blocked):
    """Review edits must wait for the maintainer to repair the baseline."""


def _agent_head(git: Git, path: Path, before: str) -> None:
    if git.head(path) != before:
        raise Blocked("Agent changed HEAD; Python must own commits")


def _read_only(git: Git, path: Path, before: str) -> None:
    _agent_head(git, path, before)
    if not git.clean(path):
        raise Blocked("Read-only agent modified the worktree")


def _workspace(settings, repository, snapshot, state, git, observer, new_agent):
    builder = new_agent()
    if observer is not None:
        builder.with_observer(observer)
    target = settings.state_dir / "worktrees" / repository.name.replace("/", "--") / str(snapshot["pr"]["number"])
    strategy = PrWorktree(target, snapshot["pr"]["head"]["sha"], state, git)
    return builder.with_git(GitOptions(repository_path=repository.path, strategy=strategy)).open()


def _recover_commit(state: PrState, git: Git, path: Path) -> None:
    batch = state.data["batch"]
    inflight = batch.get("inflight")
    if inflight:
        head = git.head(path)
        if head != inflight["parent"]:
            if (git.text(path, "rev-parse", "HEAD^") != inflight["parent"]
                    or git.text(path, "log", "-1", "--format=%s") != inflight["message"] or not git.clean(path)):
                raise Blocked("Unrecognized commit in recovery worktree")
            batch["head"] = head
            batch["index"] += 1
            batch["inflight"] = None
            state.save()
        else:
            raise Blocked("Interrupted verification/commit; inspect retained worktree before retrying")
    if not git.clean(path) or git.head(path) != batch["head"]:
        raise Blocked("Unverified edits or unexpected commits retained in recovery worktree")


def _commit_issue(state: PrState, git: Git, path: Path, message: str, replies: dict, flow, repository, timeout) -> None:
    batch = state.data["batch"]
    if git.clean(path):
        raise Blocked("Agent claimed a fix but produced no changes")
    with flow.phase("verify"):
        git.verify(path, repository, timeout)
    batch["inflight"] = {"parent": git.head(path), "message": message}
    batch["replies"].update(replies)
    state.save()
    batch["head"] = git.commit(path, message)
    batch["index"] += 1
    batch["inflight"] = None
    state.save()


def _reply_exists(snapshot: dict, item: dict, body: str, login: str) -> bool:
    if item["kind"] == "thread":
        thread = next((t for t in snapshot["threads"] if t["id"] == item["thread_id"]), None)
        if thread is None or thread["isResolved"]:
            return True
        source = item["key"].rsplit(":", 1)[-1]
        index = next((i for i, c in enumerate(thread["comments"]) if c["id"] == source), len(thread["comments"]))
        return any((c.get("author") or {}).get("login") == login and c["body"] == body
                   for c in thread["comments"][index + 1:])
    return any((c.get("author") or {}).get("login") == login and c["body"] == body for c in snapshot["other"])


def _publish(state, git, path, github, repo, number, login, flow) -> None:
    batch = state.data["batch"]
    with flow.phase("publish", head=batch["head"]):
        current = github.snapshot(repo, number)
        if current["pr"]["state"] != "open":
            raise Blocked("PR closed during publication; inspect retained work before retrying")
        if current["pr"]["head"]["ref"] != batch["branch"]:
            raise Blocked("PR branch changed during publication")
        remote_head = current["pr"]["head"]["sha"]
        if remote_head not in {batch["base"], batch["head"]}:
            raise Blocked("PR head moved; publication stopped before replying")
        if batch["head"] != batch["base"] and remote_head != batch["head"]:
            git.publish(path, batch["branch"])
        confirmed = github.json("api", f"repos/{repo}/pulls/{number}")
        if confirmed["head"]["sha"] != batch["head"]:
            raise Blocked("Published SHA could not be confirmed")
        batch["pushed"] = True
        state.save()
        for key, reply in batch["replies"].items():
            item = state.data["items"][key]
            if item["status"] == "handled":
                continue
            body = reply if item["kind"] == "thread" else f"In response to {item['url']}:\n\n{reply}"
            current = github.snapshot(repo, number)
            if item["kind"] == "thread" and not _reply_exists(current, item, body, login):
                thread = next((thread for thread in current["threads"] if thread["id"] == item["thread_id"]), None)
                source = item["key"].rsplit(":", 1)[-1]
                if thread and thread["comments"] and thread["comments"][-1]["id"] != source:
                    item["status"] = "superseded"
                    state.save()
                    continue
            if not _reply_exists(current, item, body, login):
                github.reply(repo, number, item, body)
            item["status"] = "handled"
            state.save()
        state.data["batch"] = None
        state.save()


def address_reviews(settings, repository, snapshot, items, state, github, git, login, flow, observer, new_agent) -> None:
    """Resolve issues sequentially, with Python verifying and publishing each batch."""
    address = replace(ADDRESS, timeout_sec=settings.timeout)
    with _workspace(settings, repository, snapshot, state, git, observer, new_agent) as workspace:
        path = workspace.path
        if state.data["batch"] is None:
            if not git.clean(path):
                raise Blocked("Worktree contains unrelated edits")
            with flow.phase("prepare"):
                try:
                    git.verify(path, repository, settings.timeout)
                except subprocess.CalledProcessError as exception:
                    raise BaselineFailed("Baseline verification failed") from exception
            head = git.head(path)
            with flow.phase("address", mode="group"):
                issues = group_issues(workspace, address, items)
            _read_only(git, path, head)
            state.data["batch"] = {"kind": "address", "base": head, "head": head,
                "branch": snapshot["pr"]["head"]["ref"], "issues": issues, "index": 0,
                "replies": {}, "inflight": None, "pushed": False, "unclear": False}
            state.save()
        _recover_commit(state, git, path)
        batch = state.data["batch"]
        while batch["index"] < len(batch["issues"]):
            issue = batch["issues"][batch["index"]]
            head = git.head(path)
            with flow.phase("address", issue=issue["id"]):
                result = ask(workspace, address, "resolve", {"issue": issue,
                    "items": [state.data["items"][key] for key in issue["keys"]]})
                validate_resolution(result, issue["keys"])
            _agent_head(git, path, head)
            if result["verdict"] == "fix-needed":
                _commit_issue(state, git, path, f"babysit: address {issue['id']} (PR #{snapshot['pr']['number']})",
                              result["replies"], flow, repository, settings.timeout)
            else:
                _read_only(git, path, head)
                batch["replies"].update(result["replies"])
                batch["unclear"] |= result["verdict"] == "unclear"
                batch["index"] += 1
                state.save()
        unclear = batch["unclear"]
        _publish(state, git, path, github, repository.name, snapshot["pr"]["number"], login, flow)
        if unclear:
            raise Blocked("Reviewer clarification requested")


def maintain_ci(settings, repository, snapshot, state, github, git, login, flow, observer, new_agent) -> None:
    """Diagnose failures before choosing Python-owned publication or bounded retries."""
    maintainer = replace(MAINTAINER, timeout_sec=settings.timeout)
    ci = ci_state(snapshot)
    retry_signal = digest([(run["id"], run.get("run_attempt", 1)) for run in ci["runs"]])
    if not state.data.get("batch") and state.data.get("retry_wait", {}).get(snapshot["pr"]["head"]["sha"]) == retry_signal:
        return
    with _workspace(settings, repository, snapshot, state, git, observer, new_agent) as workspace:
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
                      flow, repository, settings.timeout)
        _publish(state, git, path, github, repository.name, snapshot["pr"]["number"], login, flow)


def run(settings: Settings, repo: str, number: int, *, github=None, git=None, observer=None,
        new_agent=Agent, wait=sleep, stop=lambda: False) -> int:
    """Monitor one PR continuously; quiet polls never create telemetry phases."""
    github, git = github or Github(), git or Git()
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
                            address_reviews(settings, repository, snapshot, items, state, github, git, login, flow, observer, new_agent)
                        except BaselineFailed:
                            if not ci_state(snapshot)["failed"]:
                                raise
                            maintain_ci(settings, repository, snapshot, state, github, git, login, flow, observer, new_agent)
                            wait(settings.interval)
                        continue
                    if (batch and batch["kind"] == "ci") or ci_state(snapshot)["failed"]:
                        maintain_ci(settings, repository, snapshot, state, github, git, login, flow, observer, new_agent)
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
            except (Blocked, subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, ValueError) as exception:
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

"""Groups feedback into issues, resolves each in turn, and publishes the batch."""

from __future__ import annotations

import re
import subprocess

from loop import AgentWorkspace, WorkflowRun

from software_factory.agents import TypedAgent
from software_factory.agents.roles.address import (
    GROUP_FEEDBACK, RESOLVE_ISSUE, FeedbackIssue, Grouped, GroupRequest, Resolved, ResolveRequest,
)

from ..agents.run import run_agent
from ..agents.workspace import PrWorkspace
from ..model import BaselineFailed, Blocked, PrState
from ..publish.changes import ChangePublisher
from ..settings import Settings


def group_issues(workspace: AgentWorkspace, agent: TypedAgent[GroupRequest, Grouped], items: list[dict]) -> list[dict]:
    """Requires complete and unique feedback coverage before editing."""
    grouped = run_agent(agent, workspace, GroupRequest(items))
    if not grouped.issues:
        raise Blocked("Address returned no issues")
    keys, ids = [], []
    for issue in grouped.issues:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", issue.id) or not issue.keys:
            raise Blocked("Address returned an invalid issue")
        keys.extend(issue.keys)
        ids.append(issue.id)
    if len(ids) != len(set(ids)) or sorted(keys) != sorted(item["key"] for item in items):
        raise Blocked("Address must assign every feedback item exactly once")
    return [{"id": issue.id, "statement": issue.statement, "keys": list(issue.keys)} for issue in grouped.issues]


def validate_resolution(result: Resolved, keys: list[str]) -> None:
    """Requires a justified verdict and one usable reply per feedback item."""
    replies = {reply.key: reply.body for reply in result.replies}
    if set(replies) != set(keys) or any(not body.strip() for body in replies.values()):
        raise Blocked("Address must draft one reply for every feedback item")
    if result.verdict != "unclear" and not any(re.search(r"\S+:\d+", citation) for citation in result.evidence):
        raise Blocked("Address verdict requires path:line evidence")


class ReviewAddresser:
    """Resolves issues sequentially; Python verifies and publishes each batch."""

    def __init__(self, settings: Settings, state: PrState, workspace: PrWorkspace, changes: ChangePublisher) -> None:
        self._group = GROUP_FEEDBACK.tuned(timeout_sec=settings.timeout)
        self._resolve = RESOLVE_ISSUE.tuned(timeout_sec=settings.timeout)
        self._state = state
        self._workspace = workspace
        self._changes = changes

    def address(self, snapshot: dict, items: list[dict], login: str, flow: WorkflowRun) -> None:
        state, changes = self._state, self._changes
        with self._workspace.open(snapshot) as workspace:
            path = workspace.path
            if state.data["batch"] is None:
                if not changes.clean(path):
                    raise Blocked("Worktree contains unrelated edits")
                with flow.phase("prepare"):
                    try:
                        changes.verify(path)
                    except subprocess.CalledProcessError as exception:
                        raise BaselineFailed("Baseline verification failed") from exception
                head = changes.head(path)
                with flow.phase("address", mode="group"):
                    issues = group_issues(workspace, self._group, items)
                changes.read_only(path, head)
                state.data["batch"] = {"kind": "address", "base": head, "head": head,
                    "branch": snapshot["pr"]["head"]["ref"], "issues": issues, "index": 0,
                    "replies": {}, "inflight": None, "pushed": False, "unclear": False}
                state.save()
            changes.recover(path)
            batch = state.data["batch"]
            while batch["index"] < len(batch["issues"]):
                issue = batch["issues"][batch["index"]]
                head = changes.head(path)
                with flow.phase("address", issue=issue["id"]):
                    result = run_agent(self._resolve, workspace, ResolveRequest(
                        FeedbackIssue(issue["id"], issue.get("statement"), tuple(issue["keys"])),
                        [state.data["items"][key] for key in issue["keys"]]))
                    validate_resolution(result, issue["keys"])
                replies = {reply.key: reply.body for reply in result.replies}
                changes.agent_head(path, head)
                if result.verdict == "fix-needed":
                    changes.commit_issue(path, f"babysit: address {issue['id']} (PR #{snapshot['pr']['number']})",
                                         replies, flow)
                else:
                    changes.read_only(path, head)
                    batch["replies"].update(replies)
                    batch["unclear"] |= result.verdict == "unclear"
                    batch["index"] += 1
                    state.save()
            unclear = batch["unclear"]
            changes.publish(path, login, flow)
            if unclear:
                raise Blocked("Reviewer clarification requested")

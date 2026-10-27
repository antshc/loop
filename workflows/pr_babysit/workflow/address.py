from __future__ import annotations

import json
import re
import subprocess
from dataclasses import replace

from loop import AgentRequest

from ..agents import ADDRESS
from ..infrastructure.state import Blocked
from .changes import _agent_head, _read_only, _recover_commit, _commit_issue, _publish
from .workspace import open_workspace


class BaselineFailed(Blocked):
    """Review edits must wait for the maintainer to repair the baseline."""


def ask(workspace, profile, mode: str, payload: dict) -> dict:
    """Run one agent request and validate its JSON envelope."""
    result = workspace.agent(profile).run(AgentRequest(json.dumps({"mode": mode, **payload})))
    if result.exit_code:
        raise Blocked(f"{profile.name} exited {result.exit_code}")
    try:
        value = json.loads(result.output)
    except ValueError as exception:
        raise Blocked(f"{profile.name} returned invalid JSON") from exception
    if not isinstance(value, dict):
        raise Blocked(f"{profile.name} must return an object")
    return value


def group_issues(workspace, profile, items: list[dict]) -> list[dict]:
    """Require complete and unique feedback coverage before editing."""
    issues = ask(workspace, profile, "group", {"items": items}).get("issues")
    if not isinstance(issues, list) or not issues:
        raise Blocked("Address returned no issues")
    keys, ids = [], []
    for issue in issues:
        if (not isinstance(issue, dict) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", issue.get("id", ""))
                or not isinstance(issue.get("statement"), str) or not isinstance(issue.get("keys"), list)
                or not issue["keys"] or any(not isinstance(key, str) for key in issue["keys"])):
            raise Blocked("Address returned an invalid issue")
        keys.extend(issue["keys"])
        ids.append(issue["id"])
    if len(ids) != len(set(ids)) or sorted(keys) != sorted(item["key"] for item in items):
        raise Blocked("Address must assign every feedback item exactly once")
    return issues


def validate_resolution(result: dict, keys: list[str]) -> None:
    """Require justified verdicts and one usable reply per feedback item."""
    if result.get("verdict") not in {"fix-needed", "no-change-needed", "unclear"}:
        raise Blocked("Invalid Address verdict")
    replies = result.get("replies")
    if (not isinstance(replies, dict) or set(replies) != set(keys)
            or any(not isinstance(body, str) or not body.strip() for body in replies.values())):
        raise Blocked("Address must draft one reply for every feedback item")
    if result["verdict"] != "unclear" and not any(
            isinstance(citation, str) and re.search(r"\S+:\d+", citation)
            for citation in result.get("evidence", [])):
        raise Blocked("Address verdict requires path:line evidence")


def address_reviews(settings, repository, snapshot, items, state, github, git, login, flow, observer, new_agent, verify) -> None:
    """Resolve issues sequentially, with Python verifying and publishing each batch."""
    address = replace(ADDRESS, timeout_sec=settings.timeout)
    with open_workspace(settings, repository, snapshot, state, git, observer, new_agent) as workspace:
        path = workspace.path
        if state.data["batch"] is None:
            if not git.clean(path):
                raise Blocked("Worktree contains unrelated edits")
            with flow.phase("prepare"):
                try:
                    verify(path, repository, settings.timeout)
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
                              result["replies"], flow, repository, settings.timeout, verify)
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

from __future__ import annotations

import json
import re

from loop import AgentRequest

from ..pr_maintenance.state import Blocked


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

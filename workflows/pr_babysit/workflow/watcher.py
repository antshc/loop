from __future__ import annotations

from ..infrastructure.github import digest, trusted, remote_fingerprint as fingerprint
from ..infrastructure.state import PrState

FAILED = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}


def pending_threads(threads: list[dict], login: str) -> list[dict]:
    """Apply the Address gate before exposing comment bodies to an agent."""
    pending = []
    for thread in threads:
        comments = thread["comments"]
        if thread["isResolved"] or not comments or not trusted(comments[0], login):
            continue
        latest = comments[-1]
        own = (latest.get("author") or {}).get("login") == login
        self_note = len(comments) == 1 and own
        if own and not self_note and "fix!:" not in latest["body"].lower():
            continue
        last_reply = max((i for i, c in enumerate(comments[:-1])
                          if (c.get("author") or {}).get("login") == login), default=-1)
        pending.append({"key": f"thread:{thread['id']}:{latest['id']}", "kind": "thread",
                        "thread_id": thread["id"], "path": thread["path"],
                        "line": thread.get("line") or thread.get("originalLine"),
                        "outdated": thread.get("isOutdated", False),
                        "comments": comments[last_reply + 1:],
                        "previous_reply": comments[last_reply] if last_reply >= 0 else None})
    return pending


def observe(snapshot: dict, state: PrState, login: str) -> list[dict]:
    """Persist newly observed work independently of successful handling."""
    pending = pending_threads(snapshot["threads"], login)
    active = {item["key"] for item in pending}
    for item in snapshot["other"]:
        author = (item.get("author") or {}).get("login")
        if not trusted(item, login) or (author == login and "fix!:" not in item["body"].lower()):
            continue
        pending.append({**item, "key": f"{item['kind']}:{item['id']}:{digest(item['body'])}"})
    for key, item in state.data["items"].items():
        if item["kind"] == "thread" and key not in active and item["status"] == "pending":
            item["status"] = "superseded"
    for item in pending:
        state.data["items"].setdefault(item["key"], {**item, "status": "pending"})
    state.save()
    return [item for item in state.data["items"].values() if item["status"] == "pending"]


def latest(items: list[dict], key) -> list[dict]:
    """Select the latest occurrence of each check or workflow."""
    result = {}
    for item in sorted(items, key=lambda value: value["id"], reverse=True):
        result.setdefault(key(item), item)
    return list(result.values())


def ci_state(snapshot: dict) -> dict:
    """Separate pending, failed, and green signals on the current PR head."""
    checks = latest(snapshot["checks"], lambda c: (c["name"], (c.get("app") or {}).get("id")))
    statuses = latest(snapshot["statuses"], lambda s: s["context"])
    runs = latest(snapshot["runs"], lambda r: (r["workflow_id"], r.get("event")))
    pending = (any(c["status"] != "completed" for c in checks)
               or any(s["state"] == "pending" for s in statuses)
               or any(r["status"] != "completed" for r in runs))
    failed_runs = [r for r in runs if r.get("conclusion") in FAILED and r["status"] == "completed"]
    failed = (bool(failed_runs) or any(c.get("conclusion") in FAILED for c in checks)
              or any(s["state"] in {"failure", "error"} for s in statuses))
    return {"pending": pending, "failed": failed, "runs": failed_runs,
            "green": not pending and not failed}

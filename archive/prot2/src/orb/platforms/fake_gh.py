from __future__ import annotations

import json


class FakeGh:
    """Stands in for the `gh` binary: returns canned JSON and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: tuple[str, ...]) -> str:
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return json.dumps(
                [{"number": 10, "title": "Add login page (PR)", "url": "https://github.com/owner/repo/pull/10", "headRefName": "feature/login"}]
            )
        query = next((arg for arg in args if arg.startswith("query=")), "")
        if "labels: [" in query:
            return json.dumps([{"data": {"repository": {"issues": {"nodes": [
                _issue(1, "Add login page"),
                _issue(2, "Add logout button"),
            ]}}}}])
        if "issues(first" in query:
            return json.dumps([{"data": {"repository": {"issues": {"nodes": [
                _issue(1, "Add login page"),
                _issue(3, "Fix typo", "bug"),
            ]}}}}])
        if "reviewThreads" in query:
            return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
                _thread("t1", False, "Rename this variable."),
                _thread("t2", True, "Looks good."),
            ]}}}}})
        return "{}"


def _issue(number: int, title: str, label: str = "spec") -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/issues/{number}",
        "state": "OPEN",
        "labels": {"nodes": [{"name": label}]},
    }


def _thread(thread_id: str, resolved: bool, body: str) -> dict:
    return {
        "id": thread_id,
        "isResolved": resolved,
        "path": "src/app.py",
        "comments": {"nodes": [{"body": body}]},
    }

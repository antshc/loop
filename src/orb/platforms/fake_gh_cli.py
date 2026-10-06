from __future__ import annotations

import json


class FakeGhCli:
    """Stands in for the `gh` binary: returns canned JSON for reads and records every call."""

    def __init__(self, *, tickets: dict[int, list[dict]] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._tickets = (
            tickets
            if tickets is not None
            else {
                1: [
                    _ticket(10, "Add login form", labels=[]),
                    _ticket(11, "Add login tests", labels=["hitl"]),
                    _ticket(12, "Split login spec", labels=["spec"]),
                    _ticket(13, "Old login task", state="CLOSED", labels=[]),
                ],
            }
        )

    def __call__(self, args: tuple[str, ...]) -> str:
        self.calls.append(args)
        if args[:2] == ("pr", "list"):
            return self._pr_list(args)
        if args[:2] == ("pr", "create"):
            return "https://github.com/owner/repo/pull/99\n"
        if args and args[0] == "issue":
            return ""
        query = next((arg for arg in args if arg.startswith("query=")), "")
        if "subIssues" in query:
            number = int(next(arg for arg in args if arg.startswith("number="))[len("number=") :])
            nodes = self._tickets.get(number, [])
            return json.dumps([{"data": {"repository": {"issue": {"subIssues": {"nodes": nodes}}}}}])
        if "labels: [" in query:
            return json.dumps([{"data": {"repository": {"issues": {"nodes": [
                _ticket(1, "Add login page", labels=["spec"]),
                _ticket(2, "Add logout button", labels=["spec"]),
            ]}}}}])
        if "reviewThreads" in query:
            return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
                _thread("t1", False, "Rename this variable."),
                _thread("t2", True, "Looks good."),
            ]}}}}})
        return "{}"

    def _pr_list(self, args: tuple[str, ...]) -> str:
        if "--head" in args:
            head = args[args.index("--head") + 1]
            return json.dumps([_pr(10, "feature/login")] if head == "feature/login" else [])
        return json.dumps([_pr(10, "feature/login")])


def _ticket(number: int, title: str, *, state: str = "OPEN", labels: list[str] | None = None) -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/issues/{number}",
        "state": state,
        "labels": {"nodes": [{"name": label} for label in labels or []]},
    }


def _pr(number: int, branch: str, title: str = "Add login page (PR)") -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/pull/{number}",
        "headRefName": branch,
    }


def _thread(thread_id: str, resolved: bool, body: str) -> dict:
    return {
        "id": thread_id,
        "isResolved": resolved,
        "path": "src/app.py",
        "comments": {"nodes": [{"body": body}]},
    }

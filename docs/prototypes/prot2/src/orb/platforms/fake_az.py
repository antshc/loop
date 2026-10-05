from __future__ import annotations

import json


class FakeAz:
    """Stands in for the `az` binary: returns canned JSON and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: tuple[str, ...]) -> str:
        self.calls.append(args)
        if args[:2] == ("boards", "query"):
            return json.dumps([
                _work_item(1, "Add login page", "Active"),
                _work_item(2, "Add logout button", "New"),
            ])
        if args[:3] == ("repos", "pr", "list"):
            return json.dumps(
                [{"pullRequestId": 7, "title": "Add login page (PR)", "url": "https://dev.azure.com/org/project/_git/repo/pullrequest/7", "sourceRefName": "refs/heads/feature/login"}]
            )
        if "pullRequestThreads" in args:
            return json.dumps({"value": [
                _thread(5, "active", "Rename this variable."),
                _thread(6, "fixed", "Looks good."),
            ]})
        return "{}"


def _work_item(item_id: int, title: str, state: str) -> dict:
    return {
        "id": item_id,
        "url": f"https://dev.azure.com/org/project/_workitems/edit/{item_id}",
        "fields": {"System.Title": title, "System.State": state, "System.Tags": "spec; ready"},
    }


def _thread(thread_id: int, status: str, content: str) -> dict:
    return {
        "id": thread_id,
        "status": status,
        "threadContext": {"filePath": "/src/app.py"},
        "comments": [{"content": content}],
    }

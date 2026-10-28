"""The shared `gh` process-boundary fake: canned GraphQL/REST responses and recorded calls."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable

from workflows.shared.process import CommandError


class ScriptedGh:
    """Answers each `gh` call with `respond(args)` and records it."""

    def __init__(self, respond: Callable[[tuple[str, ...]], str]) -> None:
        self.respond = respond
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: tuple[str, ...]) -> str:
        self.calls.append(args)
        return self.respond(args)


class FakeGhCli:
    """Stands in for the `gh` binary: returns canned JSON for reads and records every call."""

    def __init__(
        self,
        *,
        tickets: dict[int, list[dict]] | None = None,
        specs: list[dict] | None = None,
        prs: list[dict] | None = None,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._created: dict[int, dict] = {}
        self._failures: list[list] = []
        # Writes mutate the stored issues, so a caller's shared fixtures must not be aliased.
        self._tickets = (
            copy.deepcopy(tickets)
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
        self._specs = (
            copy.deepcopy(specs)
            if specs is not None
            else [
                _ticket(1, "Add login page", labels=["spec"]),
                _ticket(2, "Add logout button", labels=["spec"]),
            ]
        )
        self._prs = prs if prs is not None else [_pr(10, "feature/login")]

    def fail_on(self, *prefix: str, after: int = 0) -> None:
        """Makes the call after `after` earlier matches of `prefix` exit non-zero; it is recorded, not applied."""
        self._failures.append([prefix, after])

    def writes(self) -> list[tuple[str, ...]]:
        """Every recorded call that changes GitHub, in the order it was issued."""
        return [call for call in self.calls if call[:2] in _WRITES]

    def calls_matching(self, *prefix: str) -> list[tuple[str, ...]]:
        """Every recorded call that starts with `prefix`."""
        return [call for call in self.calls if call[: len(prefix)] == prefix]

    def __call__(self, args: tuple[str, ...]) -> str:
        self.calls.append(args)
        self._raise_scheduled_failure(args)
        if args[:2] == ("pr", "list"):
            return self._pr_list(args)
        if args[:2] == ("pr", "create"):
            self._prs.append({**_pr(99, args[args.index("--head") + 1]), "body": args[args.index("--body") + 1]})
            return "https://github.com/owner/repo/pull/99\n"
        if args[:2] == ("pr", "edit"):
            for pr in self._prs:
                if str(pr["number"]) == args[2]:
                    pr["body"] = args[args.index("--body") + 1]
            return ""
        if args[:2] == ("pr", "diff"):
            return PR_DIFF
        if args[:2] == ("issue", "create"):
            number = 101 + len(self._created)
            labels = tuple(args[index + 1] for index, arg in enumerate(args) if arg == "--label")
            node = issue(number, args[args.index("--title") + 1], labels=labels)
            node["body"] = args[args.index("--body") + 1]
            self._created[number] = node
            return node["url"]
        if args[:2] == ("issue", "view"):
            return self._issue_view(int(args[2]))
        if args[:2] == ("issue", "list"):
            return self._issue_list(args[args.index("--label") + 1])
        if args[:2] in (("issue", "comment"), ("issue", "edit"), ("issue", "close")):
            self._apply_issue_write(args)
            return ""
        if args[:2] == ("api", "--method"):
            number = int(args[3].split("/")[-2])
            child = int(args[-1].split("=")[-1]) - 1000
            self._tickets.setdefault(number, []).append(self._created[child])
            return "{}"
        if args and args[0] == "api" and "--jq" in args:
            return str(1000 + int(args[1].split("/")[-1]))
        if args and args[0] == "issue":
            return ""
        query = next((arg for arg in args if arg.startswith("query=")), "")
        if "subIssues" in query:
            number = int(next(arg for arg in args if arg.startswith("number="))[len("number=") :])
            nodes = self._tickets.get(number, [])
            return json.dumps([{"data": {"repository": {"issue": {"subIssues": {"nodes": nodes}}}}}])
        if "labels: [" in query:
            return json.dumps([{"data": {"repository": {"issues": {"nodes": self._specs}}}}])
        return "{}"

    def _raise_scheduled_failure(self, args: tuple[str, ...]) -> None:
        for failure in self._failures:
            prefix, remaining = failure
            if args[: len(prefix)] != prefix:
                continue
            if remaining == 0:
                self._failures.remove(failure)
                raise CommandError("gh " + " ".join(args[:3]), 1, "scheduled failure")
            failure[1] -= 1

    def _issues(self) -> list[dict]:
        nodes = [*self._specs, *(node for tickets in self._tickets.values() for node in tickets), *self._created.values()]
        return list({node["number"]: node for node in nodes}.values())

    def _node(self, number: int) -> dict:
        return next(node for node in self._issues() if node["number"] == number)

    def _issue_view(self, number: int) -> str:
        node = self._node(number)
        return json.dumps(
            {
                "state": node["state"],
                "labels": node["labels"]["nodes"],
                "comments": [{"body": comment["body"]} for comment in node["comments"]["nodes"]],
            }
        )

    def _issue_list(self, label: str) -> str:
        open_issues = [
            node
            for node in self._issues()
            if node["state"] == "OPEN" and label in [item["name"] for item in node["labels"]["nodes"]]
        ]
        return json.dumps([{key: node[key] for key in ("number", "title", "url", "body")} for node in open_issues])

    def _apply_issue_write(self, args: tuple[str, ...]) -> None:
        node = self._node(int(args[2]))
        if args[1] == "comment":
            comment = {"author": {"login": "loop"}, "body": args[args.index("--body") + 1], "createdAt": "2026-01-02T00:00:00Z"}
            node["comments"]["nodes"].append(comment)
        elif args[1] == "close":
            node["state"] = "CLOSED"
        elif "--add-label" in args:
            node["labels"]["nodes"].append({"name": args[args.index("--add-label") + 1]})
        else:
            removed = args[args.index("--remove-label") + 1]
            node["labels"]["nodes"] = [label for label in node["labels"]["nodes"] if label["name"] != removed]

    def _pr_list(self, args: tuple[str, ...]) -> str:
        if "--head" in args:
            head = args[args.index("--head") + 1]
            return json.dumps([pr for pr in self._prs if pr["headRefName"] == head])
        return json.dumps(self._prs)


_WRITES = (
    ("issue", "create"), ("issue", "comment"), ("issue", "edit"), ("issue", "close"), ("api", "--method"),
)


def _ticket(number: int, title: str, *, state: str = "OPEN", labels: list[str] | None = None) -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/issues/{number}",
        "state": state,
        "body": f"Body of #{number}",
        "labels": {"nodes": [{"name": label} for label in labels or []]},
        "comments": {
            "nodes": [{"author": {"login": "alice"}, "body": f"Comment on #{number}", "createdAt": "2026-01-01T00:00:00Z"}]
        },
    }


def _pr(number: int, branch: str, title: str = "Add login page (PR)") -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/pull/{number}",
        "headRefName": branch,
    }


PR_DIFF = "diff --git a/src/Basket.cs b/src/Basket.cs\n--- a/src/Basket.cs\n+++ b/src/Basket.cs\n@@ -1 +1 @@\n-old\n+new\n"


def issue(number: int, title: str, *, state: str = "OPEN", labels: tuple[str, ...] = ()) -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/issues/{number}",
        "state": state,
        "body": f"Body of #{number}",
        "labels": {"nodes": [{"name": label} for label in labels]},
        "comments": {"nodes": []},
    }

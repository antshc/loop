"""The shared `gh` process-boundary fake: a stateful GitHub that serves paged REST reads and records every call."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from urllib.parse import parse_qs

from workflows.shared.process import CommandError


class ScriptedGh:
    """Answers each `gh` call with `respond(args)` and records it."""

    def __init__(self, respond: Callable[[tuple[str, ...]], str]) -> None:
        self.respond = respond
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args: tuple[str, ...]) -> str:
        self.calls.append(args)
        return self.respond(args)


_ISSUES = re.compile(r"^repos/[^/]+/[^/]+/issues(?P<rest>.*)$")


class FakeGhCli:
    """Stands in for the `gh` binary: serves issue reads in `page_size` pages, applies writes, and records every call."""

    def __init__(
        self,
        *,
        tickets: dict[int, list[dict]] | None = None,
        specs: list[dict] | None = None,
        prs: list[dict] | None = None,
        page_size: int = 100,
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.page_size = page_size
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

    def fail_on(self, *prefix: str, after: int = 0, output: str = "scheduled failure") -> None:
        """Makes the call after `after` earlier matches of `prefix` exit non-zero with `output`; it is recorded, not applied."""
        self._failures.append([prefix, after, output])

    def deny_on(self, *prefix: str) -> None:
        """Makes the next call starting with `prefix` fail the way GitHub refuses a read it may not make."""
        self.fail_on(*prefix, output="gh: Resource not accessible by integration (HTTP 403)")

    def writes(self) -> list[tuple[str, ...]]:
        """Every recorded call that changes GitHub, in the order it was issued."""
        return [call for call in self.calls if call[:2] in _WRITES]

    def calls_matching(self, *prefix: str) -> list[tuple[str, ...]]:
        """Every recorded call that starts with `prefix`."""
        return [call for call in self.calls if call[: len(prefix)] == prefix]

    def posted_comments(self) -> list[tuple[int, str]]:
        """The issue number and body of every Comment posted, in order."""
        posts = [call for call in self.calls if call[:3] == ("api", "--method", "POST") and call[3].endswith("/comments")]
        return [(int(call[3].split("/")[-2]), str(_field(call, "body"))) for call in posts]

    def created_issues(self) -> list[tuple[str, str]]:
        """The title and body of every issue created, in order."""
        posts = [call for call in self.calls if call[:3] == ("api", "--method", "POST") and call[3].endswith("/issues")]
        return [(str(_field(call, "title")), str(_field(call, "body"))) for call in posts]

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
        if args[:2] in (("issue", "edit"), ("issue", "close")):
            self._apply_issue_write(args)
            return ""
        if args and args[0] == "issue":
            return ""
        if args and args[0] == "api":
            match = next((_ISSUES.match(arg) for arg in args[1:] if _ISSUES.match(arg)), None)
            if match is not None:
                return self._issues_api(args, match["rest"])
        return "{}"

    def _raise_scheduled_failure(self, args: tuple[str, ...]) -> None:
        for failure in self._failures:
            prefix, remaining, output = failure
            if args[: len(prefix)] != prefix:
                continue
            if remaining == 0:
                self._failures.remove(failure)
                raise CommandError("gh " + " ".join(args[:3]), 1, output)
            failure[1] -= 1

    def _issues_api(self, args: tuple[str, ...], rest: str) -> str:
        path, _, query = rest.partition("?")
        if "--method" in args:
            return self._post(args, path)
        if "--paginate" in args:
            return self._paged(self._collection(path, parse_qs(query)))
        return json.dumps(_rest(self._node(int(path.lstrip("/")))))

    def _collection(self, path: str, query: dict[str, list[str]]) -> list[dict]:
        if not path:
            states, labels = query.get("state", ["open"])[0], query.get("labels", [""])[0]
            return [
                _rest(node)
                for node in self._issues()
                if states in ("all", node["state"]) and (not labels or labels in _label_names(node))
            ]
        number, _, kind = path.lstrip("/").partition("/")
        node = self._node(int(number))
        if kind == "sub_issues":
            return [_rest(child) for child in self._tickets.get(node["number"], [])]
        if kind == "labels":
            return list(node["labels"])
        return list(node["comment_list"])

    def _paged(self, items: list[dict]) -> str:
        size = self.page_size
        return json.dumps([items[start : start + size] for start in range(0, len(items), size)] or [[]])

    def _post(self, args: tuple[str, ...], path: str) -> str:
        if not path:
            return json.dumps(_rest(self._create(args)))
        number, _, kind = path.lstrip("/").partition("/")
        node = self._node(int(number))
        if kind == "comments":
            return json.dumps(self._add_comment(node, _field(args, "body"), "loop"))
        child = int(_field(args, "sub_issue_id", flag="-F")) - 1000
        self._tickets.setdefault(node["number"], []).append(self._node(child))
        return "{}"

    def _create(self, args: tuple[str, ...]) -> dict:
        number = 101 + len(self._created)
        labels = tuple(_field(args, "labels[]", all_values=True))
        node = issue(number, _field(args, "title"), labels=labels)
        node["body"] = _field(args, "body")
        self._created[number] = node
        return node

    @staticmethod
    def _add_comment(node: dict, body: str, author: str) -> dict:
        comment = {
            "id": node["number"] * 1000 + len(node["comment_list"]) + 1,
            "user": {"login": author},
            "body": body,
            "created_at": "2026-01-02T00:00:00Z",
        }
        node["comment_list"].append(comment)
        return comment

    def _issues(self) -> list[dict]:
        nodes = [*self._specs, *(node for tickets in self._tickets.values() for node in tickets), *self._created.values()]
        return list({node["number"]: node for node in nodes}.values())

    def _node(self, number: int) -> dict:
        for node in self._issues():
            if node["number"] == number:
                return node
        raise CommandError(f"gh api repos/owner/repo/issues/{number}", 1, "gh: Not Found (HTTP 404)")

    def _apply_issue_write(self, args: tuple[str, ...]) -> None:
        node = self._node(int(args[2]))
        if args[1] == "close":
            node["state"], node["state_reason"] = "closed", "completed"
        elif "--add-label" in args:
            node["labels"].append({"name": args[args.index("--add-label") + 1]})
        else:
            removed = args[args.index("--remove-label") + 1]
            node["labels"] = [label for label in node["labels"] if label["name"] != removed]

    def _pr_list(self, args: tuple[str, ...]) -> str:
        if "--head" in args:
            head = args[args.index("--head") + 1]
            return json.dumps([pr for pr in self._prs if pr["headRefName"] == head])
        return json.dumps(self._prs)


_WRITES = (
    ("issue", "edit"), ("issue", "close"), ("api", "--method"),
)


def _field(args: tuple[str, ...], name: str, *, flag: str = "-f", all_values: bool = False) -> str | list[str]:
    prefix = f"{name}="
    values = [args[index + 1][len(prefix) :] for index, arg in enumerate(args) if arg == flag and args[index + 1].startswith(prefix)]
    return values if all_values else values[0]


def _label_names(node: dict) -> list[str]:
    return [label["name"] for label in node["labels"]]


def _rest(node: dict) -> dict:
    """The REST payload of a stored issue: Comments and sub-issues are reported as counts."""
    shown = {key: value for key, value in node.items() if key not in ("comment_list", "comment_count", "sub_issue_total")}
    shown["comments"] = node.get("comment_count", len(node["comment_list"]))
    if "sub_issue_total" in node:
        shown["sub_issues_summary"] = {"total": node["sub_issue_total"]}
    return shown


def _ticket(number: int, title: str, *, state: str = "OPEN", labels: list[str] | None = None) -> dict:
    return issue(number, title, state=state, labels=tuple(labels or ()), comments=(f"Comment on #{number}",))


def _pr(number: int, branch: str, title: str = "Add login page (PR)") -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/pull/{number}",
        "headRefName": branch,
    }


PR_DIFF = "diff --git a/src/Basket.cs b/src/Basket.cs\n--- a/src/Basket.cs\n+++ b/src/Basket.cs\n@@ -1 +1 @@\n-old\n+new\n"


def issue(
    number: int,
    title: str,
    *,
    state: str = "OPEN",
    labels: tuple[str, ...] = (),
    comments: tuple[str, ...] = (),
    issue_type: str | None = None,
    pull_request: bool = False,
    close_reason: str | None = None,
    assignees: tuple[str, ...] = (),
    comment_count: int | None = None,
    sub_issue_total: int | None = None,
) -> dict:
    """A stored issue in GitHub's REST shape. `comment_count` and `sub_issue_total` override the counts GitHub reports, to simulate a cut-short page."""
    node = {
        "id": 1000 + number,
        "node_id": f"I_kwDO{number}",
        "number": number,
        "title": title,
        "html_url": f"https://github.com/owner/repo/issues/{number}",
        "state": state.lower(),
        "state_reason": close_reason,
        "body": f"Body of #{number}",
        "labels": [{"name": label} for label in labels],
        "assignees": [{"login": login} for login in assignees],
        "type": None if issue_type is None else {"name": issue_type},
        "comment_list": [
            {
                "id": number * 1000 + index + 1,
                "user": {"login": "alice"},
                "body": body,
                "created_at": "2026-01-01T00:00:00Z",
            }
            for index, body in enumerate(comments)
        ],
    }
    if pull_request:
        node["pull_request"] = {"url": f"https://api.github.com/repos/owner/repo/pulls/{number}"}
    if comment_count is not None:
        node["comment_count"] = comment_count
    if sub_issue_total is not None:
        node["sub_issue_total"] = sub_issue_total
    return node

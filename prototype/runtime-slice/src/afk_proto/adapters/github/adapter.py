from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from afk_proto.runtime.contracts.platform_adapter import PlatformAdapter, PullRequest, ReviewThread, WorkItem

GhRunner = Callable[[tuple[str, ...]], str]

_SPECS_QUERY = (
    "query($owner: String!, $repo: String!) {"
    " repository(owner: $owner, name: $repo) {"
    '  issues(first: 100, states: OPEN, labels: ["spec"]) {'
    "   nodes { number title url state labels(first: 20) { nodes { name } } }"
    " } } }"
)
_THREADS_QUERY = (
    "query($owner: String!, $repo: String!, $number: Int!) {"
    " repository(owner: $owner, name: $repo) {"
    "  pullRequest(number: $number) { reviewThreads(first: 100) {"
    "   nodes { id isResolved path comments(first: 50) { nodes { body } } }"
    " } } } }"
)
_REPLY_MUTATION = (
    "mutation($thread: ID!, $body: String!) {"
    " addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, body: $body}) {"
    "  comment { id } } }"
)


class GitHubAdapter(PlatformAdapter):
    """Translates the platform-neutral contract to `gh`; no GitHub shape leaves this class."""

    def __init__(self, owner: str, repo: str, *, gh: GhRunner, dry_run: bool = False) -> None:
        self._owner = owner
        self._repo = repo
        self._gh = gh
        self._dry_run = dry_run

    def list_specs(self) -> list[WorkItem]:
        pages = json.loads(self._graphql(_SPECS_QUERY, paginate=True))
        return [
            WorkItem(
                id=str(node["number"]),
                title=node["title"],
                state=node["state"].lower(),
                tags=tuple(label["name"] for label in node["labels"]["nodes"]),
                url=node["url"],
            )
            for page in pages
            for node in page["data"]["repository"]["issues"]["nodes"]
        ]

    def list_pull_requests(self) -> list[PullRequest]:
        output = self._gh(
            (
                "pr", "list",
                "--repo", f"{self._owner}/{self._repo}",
                "--state", "open",
                "--json", "number,title,url",
            )
        )
        return [
            PullRequest(id=str(pr["number"]), title=pr["title"], url=pr["url"])
            for pr in json.loads(output)
        ]

    def review_threads(self, pull_request_id: str) -> list[ReviewThread]:
        data: dict[str, Any] = json.loads(self._graphql(_THREADS_QUERY, number=int(pull_request_id)))
        nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        return [
            ReviewThread(
                id=node["id"],
                path=node["path"],
                body="\n".join(comment["body"] for comment in node["comments"]["nodes"]),
                resolved=node["isResolved"],
            )
            for node in nodes
        ]

    def reply_to_thread(self, pull_request_id: str, thread_id: str, body: str) -> None:
        if self._dry_run:
            return
        self._graphql(_REPLY_MUTATION, scoped=False, thread=thread_id, body=body)

    def _graphql(self, query: str, *, paginate: bool = False, scoped: bool = True, **variables: str | int) -> str:
        args = ["api", "graphql"]
        if paginate:
            args += ["--paginate", "--slurp"]
        args += ["-f", f"query={query}"]
        if scoped:
            args += ["-f", f"owner={self._owner}", "-f", f"repo={self._repo}"]
        for name, value in variables.items():
            flag = "-F" if isinstance(value, int) else "-f"
            args += [flag, f"{name}={value}"]
        return self._gh(tuple(args))

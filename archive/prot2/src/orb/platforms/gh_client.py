from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orb.process import cli_runner, run_command

GhRunner = Callable[[tuple[str, ...]], str]


@dataclass(frozen=True)
class WorkItem:
    id: str
    title: str
    state: str
    tags: tuple[str, ...]
    url: str


@dataclass(frozen=True)
class PullRequest:
    id: str
    title: str
    url: str
    branch: str


@dataclass(frozen=True)
class ReviewThread:
    id: str
    path: str
    body: str
    resolved: bool

_ISSUES_QUERY = (
    "query($owner: String!, $repo: String!) {"
    " repository(owner: $owner, name: $repo) {"
    "  issues(first: 100, states: OPEN) {"
    "   nodes { number title url state labels(first: 20) { nodes { name } } }"
    " } } }"
)
_SPECS_QUERY = (
    "query($owner: String!, $repo: String!) {"
    " repository(owner: $owner, name: $repo) {"
    '  issues(first: 100, states: OPEN, labels: ["spec"]) {'
    "   nodes { number title url state labels(first: 20) { nodes { name } } }"
    " } } }"
)
_REMOTE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")
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


class GitHubClient:
    """Specs, issues, and pull requests through `gh`; no GitHub shape leaves this class."""

    def __init__(self, owner: str, repo: str, *, gh: GhRunner, dry_run: bool = False) -> None:
        self._owner = owner
        self._repo = repo
        self._gh = gh
        self._dry_run = dry_run

    @classmethod
    def for_repo(cls, repo: Path, *, dry_run: bool = False) -> GitHubClient:
        """Client for the GitHub repository behind `origin`."""
        url = run_command(("git", "remote", "get-url", "origin"), cwd=repo).strip()
        match = _REMOTE.search(url)
        if match is None:
            raise ValueError(f"unsupported remote: {url}")
        return cls(match["owner"], match["repo"], gh=cli_runner("gh", cwd=repo), dry_run=dry_run)

    def get_specs(self) -> list[WorkItem]:
        return self._work_items(_SPECS_QUERY)

    def get_issues(self) -> list[WorkItem]:
        return self._work_items(_ISSUES_QUERY)

    def get_pull_requests(self) -> list[PullRequest]:
        output = self._gh(
            (
                "pr", "list",
                "--repo", f"{self._owner}/{self._repo}",
                "--state", "open",
                "--json", "number,title,url,headRefName",
            )
        )
        return [
            PullRequest(id=str(pr["number"]), title=pr["title"], url=pr["url"], branch=pr["headRefName"])
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

    def _work_items(self, query: str) -> list[WorkItem]:
        pages = json.loads(self._graphql(query, paginate=True))
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

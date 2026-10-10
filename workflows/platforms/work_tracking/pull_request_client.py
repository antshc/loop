from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .github_repo import GitHubRepo

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


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    url: str
    branch: str


@dataclass(frozen=True)
class ReviewThread:
    id: str
    path: str
    body: str
    resolved: bool


class PullRequestClient:
    """One repository's pull requests through `gh`."""

    def __init__(self, repo: GitHubRepo) -> None:
        self._repo = repo

    def find_pull_request(self, head_branch: str) -> PullRequest | None:
        """The open PR from `head_branch`, or None."""
        output = self._repo.run(
            (
                "pr", "list",
                "--repo", self._repo.slug,
                "--state", "open",
                "--head", head_branch,
                "--json", "number,title,url,headRefName",
            )
        )
        prs = json.loads(output)
        if not prs:
            return None
        pr = prs[0]
        return PullRequest(number=pr["number"], title=pr["title"], url=pr["url"], branch=pr["headRefName"])

    def create_draft_pull_request(self, head: str, base: str, title: str, body: str = "") -> PullRequest:
        """One draft PR per head branch: returns the open PR for `head` when there is one, else creates it."""
        existing = self.find_pull_request(head)
        if existing is not None:
            return existing
        output = self._repo.run(
            (
                "pr", "create",
                "--repo", self._repo.slug,
                "--head", head,
                "--base", base,
                "--title", title,
                "--body", body,
                "--draft",
            )
        )
        match = re.search(r"/pull/(\d+)", output.strip())
        number = int(match.group(1)) if match else 0
        return PullRequest(number=number, title=title, url=output.strip(), branch=head)

    def update_pull_request(self, number: int, *, title: str | None = None, body: str | None = None) -> None:
        """Edits the PR's title and/or body; omitted fields stay unchanged."""
        args = ["pr", "edit", str(number), "--repo", self._repo.slug]
        if title is not None:
            args += ["--title", title]
        if body is not None:
            args += ["--body", body]
        self._repo.run(tuple(args))

    def review_threads(self, pull_request_id: str) -> list[ReviewThread]:
        """Every review thread on PR number `pull_request_id`, resolved or not."""
        data: dict[str, Any] = json.loads(self._repo.graphql(_THREADS_QUERY, number=int(pull_request_id)))
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
        """Replies `body` on review thread `thread_id`."""
        self._repo.graphql(_REPLY_MUTATION, scoped=False, thread=thread_id, body=body)

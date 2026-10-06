from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loop.process import cli_runner, run_command

logger = logging.getLogger("loop.platforms.github")

_BLOCKING_LABELS = frozenset({"hitl", "spec"})
_ISSUE_FIELDS = "number title url state labels(first: 20) { nodes { name } }"


@dataclass(frozen=True)
class Spec:
    number: int
    title: str
    url: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Ticket:
    number: int
    title: str
    state: str
    labels: tuple[str, ...]
    url: str


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


class GhCli:
    """Wraps one `gh` invocation; the only place `gh` is started."""

    def __init__(self, *, cwd: Path | None = None) -> None:
        self._call = cli_runner("gh", cwd=cwd)

    def __call__(self, args: tuple[str, ...]) -> str:
        return self._call(args)


GhRunner = Callable[[tuple[str, ...]], str]

_SPECS_QUERY = (
    "query($owner: String!, $repo: String!) {"
    " repository(owner: $owner, name: $repo) {"
    f'  issues(first: 100, states: OPEN, labels: ["spec"]) {{ nodes {{ {_ISSUE_FIELDS} }} }}'
    " } }"
)
_SUB_ISSUES_QUERY = (
    "query($owner: String!, $repo: String!, $number: Int!) {"
    " repository(owner: $owner, name: $repo) {"
    f"  issue(number: $number) {{ subIssues(first: 100) {{ nodes {{ {_ISSUE_FIELDS} }} }} }}"
    " } }"
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
    """Specs, Tickets, and pull requests through `gh`; no GitHub shape leaves this class."""

    def __init__(self, owner: str, repo: str, *, gh: GhRunner, dry_run: bool = False) -> None:
        self._owner = owner
        self._repo = repo
        self._gh = gh
        self._dry_run = dry_run

    @classmethod
    def for_repo(
        cls, harness_repo: Path, target_repo: Path | None = None, *, dry_run: bool = False
    ) -> tuple[GitHubClient, GitHubClient]:
        """The harness client (Specs, Tickets) and the target client (pull requests); the same repo by default."""
        harness = cls._from_origin(harness_repo, dry_run=dry_run)
        if target_repo is None or target_repo == harness_repo:
            return harness, harness
        return harness, cls._from_origin(target_repo, dry_run=dry_run)

    @classmethod
    def _from_origin(cls, repo: Path, *, dry_run: bool) -> GitHubClient:
        url = run_command(("git", "remote", "get-url", "origin"), cwd=repo).strip()
        match = _REMOTE.search(url)
        if match is None:
            raise ValueError(f"unsupported remote: {url}")
        return cls(match["owner"], match["repo"], gh=GhCli(cwd=repo), dry_run=dry_run)

    def get_specs(self) -> list[Spec]:
        pages = json.loads(self._graphql(_SPECS_QUERY, paginate=True))
        return [
            Spec(
                number=node["number"],
                title=node["title"],
                url=node["url"],
                labels=tuple(label["name"] for label in node["labels"]["nodes"]),
            )
            for page in pages
            for node in page["data"]["repository"]["issues"]["nodes"]
        ]

    def get_tickets(self, spec: Spec) -> list[Ticket]:
        pages = json.loads(self._graphql(_SUB_ISSUES_QUERY, paginate=True, number=spec.number))
        return [
            Ticket(
                number=node["number"],
                title=node["title"],
                state=node["state"].lower(),
                labels=tuple(label["name"] for label in node["labels"]["nodes"]),
                url=node["url"],
            )
            for page in pages
            for node in page["data"]["repository"]["issue"]["subIssues"]["nodes"]
        ]

    def get_actionable_issues(self, spec: Spec) -> list[Ticket]:
        """A Spec's open sub-issues that carry neither the `hitl` nor the `spec` label."""
        return [
            ticket
            for ticket in self.get_tickets(spec)
            if ticket.state == "open" and not ({label.casefold() for label in ticket.labels} & _BLOCKING_LABELS)
        ]

    def find_pull_request(self, head_branch: str) -> PullRequest | None:
        output = self._gh(
            (
                "pr", "list",
                "--repo", self._slug,
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

    def comment(self, number: int, body: str) -> None:
        self._write(("issue", "comment", str(number), "--repo", self._slug, "--body", body))

    def add_label(self, number: int, label: str) -> None:
        self._write(("issue", "edit", str(number), "--repo", self._slug, "--add-label", label))

    def close_with_comment(self, number: int, body: str) -> None:
        self._write(("issue", "close", str(number), "--repo", self._slug, "--comment", body))

    def create_draft_pull_request(self, head: str, base: str, title: str, body: str = "") -> PullRequest | None:
        output = self._write(
            (
                "pr", "create",
                "--repo", self._slug,
                "--head", head,
                "--base", base,
                "--title", title,
                "--body", body,
                "--draft",
            )
        )
        if output is None:
            return None
        match = re.search(r"/pull/(\d+)", output.strip())
        number = int(match.group(1)) if match else 0
        return PullRequest(number=number, title=title, url=output.strip(), branch=head)

    def update_pull_request(self, number: int, *, title: str | None = None, body: str | None = None) -> None:
        args = ["pr", "edit", str(number), "--repo", self._slug]
        if title is not None:
            args += ["--title", title]
        if body is not None:
            args += ["--body", body]
        self._write(tuple(args))

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
            logger.info("dry-run: skipped reply to thread %s", thread_id)
            return
        self._graphql(_REPLY_MUTATION, scoped=False, thread=thread_id, body=body)

    @property
    def _slug(self) -> str:
        return f"{self._owner}/{self._repo}"

    def _write(self, args: tuple[str, ...]) -> str | None:
        if self._dry_run:
            logger.info("dry-run: skipped gh %s", " ".join(args))
            return None
        return self._gh(args)

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

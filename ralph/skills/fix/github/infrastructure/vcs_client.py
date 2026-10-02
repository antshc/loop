"""Thin wrapper around the VCS host CLI (`gh`) for GraphQL and REST operations."""

import logging

from ..domain.comment import Comment
from ..domain.issue import Issue
from ..domain.issue_comment import IssueComment
from ..domain.pull_request import PullRequest
from ..domain.review_thread import ReviewThread
from .gh_cli import GhCli
from ..shared.pr_url import parse_pr_url

logger = logging.getLogger(__name__)


class VCSClient:
    """Thin wrapper around the VCS host CLI for PR and review thread operations."""

    def __init__(self, *, gh: GhCli | None = None) -> None:
        self._gh = gh or GhCli()

    def list_prs(self, user: str, repo: str) -> list[PullRequest]:
        """Return open PRs authored by *user* in *repo* as PullRequest domain objects."""
        prs = self._gh.pr_list(user, repo)
        result = []
        for pr in prs:
            url = pr["url"]
            owner, repo_name, number = parse_pr_url(url)
            result.append(PullRequest(owner=owner, repo=repo_name, number=number, url=url, title=pr.get("title", "")))
        return result

    def checkout_pr(self, pr_url: str) -> None:
        """Check out a PR branch locally."""
        self._gh.pr_checkout(pr_url)

    def fetch_review_threads(self, owner: str, repo: str, number: int) -> list[ReviewThread]:
        """Fetch review threads for a PR via GraphQL."""
        nodes = self._gh.fetch_threads_raw(owner, repo, number)
        return [self._thread_from_raw(node) for node in nodes]

    def fetch_issues(self, owner: str, repo: str, spec_number: int | None = None) -> list[Issue]:
        """Fetch open issues via GraphQL — the open sub-issues of *spec_number* when given."""
        nodes = self._gh.fetch_issues_raw(owner, repo, spec_number)
        return [self._issue_from_raw(node) for node in nodes]

    def list_specs(self, owner: str, repo: str) -> list[Issue]:
        """Fetch open issues labelled `spec` via GraphQL."""
        nodes = self._gh.list_specs_raw(owner, repo)
        return [self._issue_from_raw(node) for node in nodes]

    def _thread_from_raw(self, raw: dict) -> ReviewThread:
        """Map a raw GitHub API thread dict to a ReviewThread domain entity."""
        comments = [Comment(author=c["author"]["login"], body=c["body"]) for c in raw.get("comments", [])]
        start = raw.get("startLine") or raw.get("line")
        end = raw.get("line")
        return ReviewThread(
            thread_id=raw["id"],
            path=raw.get("path", ""),
            lines=f"{start}-{end}",
            is_resolved=raw.get("isResolved", False),
            comments=comments,
        )

    def _issue_from_raw(self, raw: dict) -> Issue:
        """Map a raw GitHub API issue dict to an Issue domain entity."""
        labels = [label["name"] if isinstance(label, dict) else label for label in raw.get("labels", [])]
        comments = [
            IssueComment(
                id=comment["id"],
                body=comment.get("body", ""),
                created_at=comment.get("createdAt", comment.get("created_at", "")),
            )
            for comment in raw.get("comments", [])
        ]
        return Issue(
            number=raw["number"],
            title=raw.get("title", ""),
            body=raw.get("body", ""),
            url=raw["url"],
            labels=labels,
            comments=comments,
        )

"""The authenticated `gh` account and the repositories it reaches."""

from __future__ import annotations

import json

from .issues.client import Issues
from .pull_requests import PublishedPullRequest, PullRequests
from .repo import GhRunner, GitHubRepo


class GitHub:
    """The authenticated `gh` account and the repositories it reaches."""

    def __init__(self, gh: GhRunner) -> None:
        self._gh = gh

    def login(self) -> str:
        """The authenticated actor."""
        return json.loads(self._gh(("api", "user")))["login"]

    def repository(self, slug: str) -> GitHubRepo:
        """The repository named `owner/name`."""
        return GitHubRepo.from_slug(slug, self._gh)

    def issues(self, slug: str) -> Issues:
        """The issues of the repository named `owner/name`."""
        return Issues(self.repository(slug))

    def pull_requests(self, slug: str) -> PullRequests:
        """The pull requests of the repository named `owner/name`."""
        return PullRequests(self.repository(slug))

    def open_pull_request(self, slug: str, branch: str) -> PublishedPullRequest | None:
        """The open pull request whose head is `branch` in the repository named `owner/name`, or None."""
        return self.pull_requests(slug).find_open(branch)

    def require_open_pull_request(self, slug: str, branch: str) -> PublishedPullRequest:
        """The open pull request whose head is `branch`; raises LookupError when there is none."""
        found = self.open_pull_request(slug, branch)
        if found is None:
            raise LookupError(f"no open pull request for {branch}")
        return found

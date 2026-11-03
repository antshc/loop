"""Compose factory GitHub capabilities over the installed repository transport."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loop.software_factory.infrastructure.github.account import GitHub as GitHubAccount

if TYPE_CHECKING:
    from software_factory.src.pull_requests.infrastructure.github.pull_requests import (
        PublishedPullRequest,
        PullRequests,
    )
    from software_factory.src.spec_delivery.infrastructure.github.issues.client import (
        Issues,
    )


class GitHub(GitHubAccount):
    """The account with this factory's issue and pull-request capabilities."""

    def issues(self, slug: str) -> Issues:
        """The issues of the repository named `owner/name`."""
        from software_factory.src.spec_delivery.infrastructure.github.issues.client import (
            Issues,
        )

        return Issues(self.repository(slug))

    def pull_requests(self, slug: str) -> PullRequests:
        """The pull requests of the repository named `owner/name`."""
        from software_factory.src.pull_requests.infrastructure.github.pull_requests import (
            PullRequests,
        )

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

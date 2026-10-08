"""A Spec's draft pull request on its target repo."""

from __future__ import annotations

from .gh_client import GitHubClient
from .tracker import Spec


class PullRequests:
    """The target repo's draft pull requests; hides which repo hosts them and how titles are formed."""

    def __init__(self, github: GitHubClient) -> None:
        self._github = github

    def publish_draft(self, spec: Spec, feature_branch: str) -> str:
        """URL of the Spec's draft PR from `feature_branch` into its base branch, created when none is open.

        Titles it `<initiative>: <bare title>`; an existing PR is reused unchanged.
        """
        title = f"{spec.initiative}: {spec.bare_title}"
        return self._github.create_draft_pull_request(feature_branch, spec.base_branch, title).url

"""The routed Spec's feature-branch pull request, found the one way every Spec Stage finds it."""

from __future__ import annotations

from software_factory.events.specs import SpecRouted
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.pull_requests import PublishedPullRequest


def open_pull_request(github: GitHub, routed: SpecRouted) -> PublishedPullRequest | None:
    """The open pull request of the routed feature branch, or None when there is none."""
    return github.pull_requests(routed.target.slug).find_open(routed.feature_branch)


def require_open_pull_request(github: GitHub, routed: SpecRouted) -> PublishedPullRequest:
    """The open pull request of the routed feature branch; raises LookupError when there is none."""
    found = open_pull_request(github, routed)
    if found is None:
        raise LookupError(f"no open pull request for {routed.feature_branch}")
    return found

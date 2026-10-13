from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .github_repo import GitHubRepo

@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    url: str
    branch: str


class PullRequestClient:
    """Creates draft PRs or reuses open PRs in one repository through `gh`."""

    def __init__(self, repo: GitHubRepo) -> None:
        self._repo = repo

    def create_draft_pull_request(self, head: str, base: str, title: str, body: str = "") -> PullRequest:
        """One draft PR per head branch: returns the open PR for `head` when there is one, else creates it."""
        existing = self._find_pull_request(head)
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

    def _find_pull_request(self, head_branch: str) -> PullRequest | None:
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

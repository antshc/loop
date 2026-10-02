"""Utility for parsing GitHub PR URLs."""

from urllib.parse import urlparse


def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """Extract (owner, repo, number) from a GitHub PR URL."""
    path = urlparse(pr_url).path.strip("/").split("/")
    return path[0], path[1], int(path[3])

from __future__ import annotations


def discover(github, repositories, author: str) -> list[tuple[str, dict]]:
    """Return a complete discovery scan before advancing the startup baseline."""
    return [(repo.name, pr) for repo in repositories for pr in github.open_prs(repo.name, author)]

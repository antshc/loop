from __future__ import annotations

import re
from pathlib import Path

from orb.contracts.platform_adapter import PlatformAdapter
from orb.platforms.azure_devops import AzRunner, AzureDevOpsAdapter
from orb.platforms.github import GhRunner, GitHubAdapter
from orb.process import cli_runner, run_command

_GITHUB_REMOTE = re.compile(r"^(?:git@github\.com:|https://github\.com/)(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")
_AZURE_REMOTE = re.compile(
    r"^https://(?:[^@/]+@)?dev\.azure\.com/(?P<org>[^/]+)/(?P<project>[^/]+)/_git/(?P<repo>[^/]+)$"
)


def platform_from_remote(
    remote_url: str,
    *,
    dry_run: bool = False,
    gh: GhRunner | None = None,
    az: AzRunner | None = None,
) -> PlatformAdapter:
    """Pick the adapter from the remote URL; `gh`/`az` default to the real binaries."""
    azure = _AZURE_REMOTE.match(remote_url)
    if azure:
        return AzureDevOpsAdapter(
            azure["org"], azure["project"], azure["repo"], az=az or cli_runner("az"), dry_run=dry_run
        )
    github = _GITHUB_REMOTE.match(remote_url)
    if github:
        return GitHubAdapter(github["owner"], github["repo"], gh=gh or cli_runner("gh"), dry_run=dry_run)
    raise ValueError(f"unsupported remote: {remote_url}")


def platform_for_repo(repo: Path, *, dry_run: bool = False) -> PlatformAdapter:
    remote_url = run_command(("git", "remote", "get-url", "origin"), cwd=repo).strip()
    return platform_from_remote(remote_url, dry_run=dry_run)

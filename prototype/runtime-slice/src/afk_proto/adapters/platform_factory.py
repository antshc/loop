from __future__ import annotations

import re

from afk_proto.adapters.azure_devops.adapter import AzureDevOpsAdapter
from afk_proto.adapters.azure_devops.fake_az import FakeAz
from afk_proto.adapters.github.adapter import GitHubAdapter
from afk_proto.adapters.github.fake_gh import FakeGh
from afk_proto.runtime.context import RunContext
from afk_proto.runtime.contracts.platform_adapter import PlatformAdapter

_GITHUB_REMOTE = re.compile(r"^(?:git@github\.com:|https://github\.com/)(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$")
_AZURE_REMOTE = re.compile(
    r"^https://(?:[^@/]+@)?dev\.azure\.com/(?P<org>[^/]+)/(?P<project>[^/]+)/_git/(?P<repo>[^/]+)$"
)


def create_platform_adapter(ctx: RunContext) -> PlatformAdapter:
    azure = _AZURE_REMOTE.match(ctx.remote_url)
    if azure:
        return AzureDevOpsAdapter(
            azure["org"], azure["project"], azure["repo"], az=FakeAz(), dry_run=ctx.dry_run
        )
    github = _GITHUB_REMOTE.match(ctx.remote_url)
    if github:
        return GitHubAdapter(github["owner"], github["repo"], gh=FakeGh(), dry_run=ctx.dry_run)
    raise ValueError(f"unsupported remote: {ctx.remote_url}")

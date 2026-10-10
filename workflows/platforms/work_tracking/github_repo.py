from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from ..process import cli_runner, run_command

_SLUG_REMOTE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


class GhCli:
    """Wraps one `gh` invocation; the only place `gh` is started."""

    def __init__(self, *, cwd: Path | None = None) -> None:
        self._call = cli_runner("gh", cwd=cwd)

    def __call__(self, args: tuple[str, ...]) -> str:
        return self._call(args)


GhRunner = Callable[[tuple[str, ...]], str]


class GitHubRepo:
    """One GitHub repository reached through `gh`; the shared seam under the issue and pull-request clients."""

    def __init__(self, owner: str, name: str, *, gh: GhRunner) -> None:
        self.owner = owner
        self.name = name
        self._gh = gh

    @classmethod
    def from_origin(cls, checkout: Path) -> GitHubRepo:
        """The repo behind `checkout`'s `origin` remote; ValueError when it is not a GitHub remote."""
        url = run_command(("git", "remote", "get-url", "origin"), cwd=checkout).strip()
        match = _SLUG_REMOTE.search(url)
        if match is None:
            raise ValueError(f"unsupported remote: {url}")
        return cls(match["owner"], match["repo"], gh=GhCli(cwd=checkout))

    @property
    def slug(self) -> str:
        """`owner/name`, as `gh --repo` expects it."""
        return f"{self.owner}/{self.name}"

    def run(self, args: tuple[str, ...]) -> str:
        """Runs `gh <args>` and returns its stdout."""
        return self._gh(args)

    def graphql(self, query: str, *, paginate: bool = False, scoped: bool = True, **variables: str | int) -> str:
        """Runs a GraphQL query; `scoped` also passes the `$owner`/`$repo` variables."""
        args = ["api", "graphql"]
        if paginate:
            args += ["--paginate", "--slurp"]
        args += ["-f", f"query={query}"]
        if scoped:
            args += ["-f", f"owner={self.owner}", "-f", f"repo={self.name}"]
        for name, value in variables.items():
            flag = "-F" if isinstance(value, int) else "-f"
            args += [flag, f"{name}={value}"]
        return self._gh(tuple(args))

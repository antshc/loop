"""`gh` invocation and one-repository GitHub access."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..git import github_slug
from ..process import Runner


GhRunner = Callable[[tuple[str, ...]], str]


class GhCli:
    """Starts `gh` through the injected process runner; the only place `gh` is started."""

    def __init__(self, run: Runner, *, cwd: Path | None = None) -> None:
        self._run = run
        self._cwd = cwd

    def __call__(self, args: tuple[str, ...]) -> str:
        return self._run(("gh", *args), cwd=self._cwd)


class GitHubRepo:
    """One GitHub repository reached through `gh`; the seam under the issue and pull-request clients."""

    def __init__(self, owner: str, name: str, *, gh: GhRunner) -> None:
        self.owner = owner
        self.name = name
        self._gh = gh

    @classmethod
    def from_slug(cls, slug: str, gh: GhRunner) -> GitHubRepo:
        """The repository named `owner/name`; ValueError for any other shape."""
        owner, separator, name = github_slug(slug).partition("/")
        if not separator or not owner or not name or "/" in name or ":" in owner:
            raise ValueError(f"unsupported repository: {slug}")
        return cls(owner, name, gh=gh)

    @property
    def slug(self) -> str:
        """`owner/name`, as `gh --repo` expects it."""
        return f"{self.owner}/{self.name}"

    def run(self, args: tuple[str, ...]) -> str:
        """Runs `gh <args>` and returns its stdout."""
        return self._gh(args)

    def json(self, *args: str) -> Any:
        """Decodes a `gh` JSON response; ValueError when the API reports errors."""
        value = json.loads(self.run(args))
        if isinstance(value, dict) and value.get("errors"):
            raise ValueError(f"GitHub API errors: {value.get('errors')}")
        return value

    def pages(self, endpoint: str) -> list[dict]:
        """Every item of every page of a REST collection."""
        return [item for page in self.json("api", "--paginate", "--slurp", endpoint) for item in page]

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

"""address_once's configuration: the repositories whose pull requests it may address and how it checks its work, loaded from a JSON file."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Repository:
    """Pair an explicit GitHub repository with its checkout and test command."""

    name: str
    path: Path
    test: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    """Configure one addressing run: the repositories, where its ledger and worktrees live and how long a check may take."""

    repositories: tuple[Repository, ...]
    state_dir: Path
    timeout: float = 1800

    @property
    def ledger_root(self) -> Path:
        """Where every pull request's Execution ledger lives."""
        return self.state_dir / "ledger"

    @classmethod
    def load(cls, path: Path) -> Settings:
        """Resolve JSON configuration paths relative to the configuration file."""
        path = path.resolve()
        value = json.loads(path.read_text(encoding="utf-8"))
        repositories = tuple(
            _repository(item, path.parent) for item in value["repositories"]
        )
        settings = cls(
            repositories,
            (path.parent / value.get("state_dir", ".loop/address-once")).resolve(),
            float(value.get("timeout", 1800)),
        )
        if not repositories or len({repo.name for repo in repositories}) != len(
            repositories
        ):
            raise ValueError("configure at least one repository, without duplicates")
        if not math.isfinite(settings.timeout) or settings.timeout <= 0:
            raise ValueError("invalid timeout")
        return settings

    def repository(self, name: str) -> Repository:
        """Reject unconfigured repositories before any Git operation."""
        return next(
            (repo for repo in self.repositories if repo.name == name), None
        ) or self._missing(name)

    def _missing(self, name: str) -> Repository:
        raise ValueError(f"Repository not configured: {name}")


def _repository(item: dict, base: Path) -> Repository:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", item["name"]):
        raise ValueError("repository name must be owner/repo")
    test = item.get("test")
    if (
        not isinstance(test, list)
        or not test
        or any(not isinstance(part, str) or not part for part in test)
    ):
        raise ValueError("each repository requires a test argument array")
    return Repository(item["name"], (base / item["path"]).resolve(), tuple(test))

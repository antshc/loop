"""pr_babysit's configuration: the repositories it may maintain and how it paces, bounds and checks its work, loaded from a JSON file."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Repository:
    """Pair an explicit GitHub repository with its checkout and verification commands."""

    name: str
    path: Path
    build: tuple[str, ...]
    test: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    """Configure discovery and bounded maintenance without library-global defaults."""

    repositories: tuple[Repository, ...]
    state_dir: Path
    author: str = ""
    interval: float = 60
    concurrency: int = 2
    retries: int = 3
    copilot_review: bool = False
    otel_endpoint: str | None = None
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
        repositories = []
        for item in value["repositories"]:
            if not re.fullmatch(r"[\w.-]+/[\w.-]+", item["name"]):
                raise ValueError("repository name must be owner/repo")
            commands = [item.get("build"), item.get("test")]
            if any(not isinstance(command, list) or not command or
                   any(not isinstance(part, str) or not part for part in command) for command in commands):
                raise ValueError("each repository requires build and test argument arrays")
            repositories.append(Repository(item["name"], (path.parent / item["path"]).resolve(),
                                           tuple(commands[0]), tuple(commands[1])))
        settings = cls(tuple(repositories), (path.parent / value.get("state_dir", ".loop/pr-babysit")).resolve(),
                       value.get("author", ""), float(value.get("interval", 60)),
                       int(value.get("concurrency", 2)), int(value.get("retries", 3)),
                       bool(value.get("copilot_review", False)), value.get("otel_endpoint"),
                       float(value.get("timeout", 1800)))
        if not repositories or len({repo.name for repo in repositories}) != len(repositories):
            raise ValueError("configure at least one repository, without duplicates")
        if (not math.isfinite(settings.interval) or not math.isfinite(settings.timeout)
                or settings.interval <= 0 or settings.concurrency < 1 or settings.retries < 0 or settings.timeout <= 0):
            raise ValueError("invalid interval, concurrency, retries, or timeout")
        return settings

    def repository(self, name: str) -> Repository:
        """Reject unconfigured repositories before any Git operation."""
        return next((repo for repo in self.repositories if repo.name == name), None) or self._missing(name)

    def _missing(self, name: str) -> Repository:
        raise ValueError(f"Repository not configured: {name}")

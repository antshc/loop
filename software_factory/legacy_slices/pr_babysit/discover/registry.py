"""The discovery baseline: which PRs are enrolled, each exactly once."""

from __future__ import annotations

from pathlib import Path

from software_factory.shared.github.pull_requests.model import PullRequest
from software_factory.shared.state import JsonFile

from ..model import Blocked, PrState, read_state


class Registry:
    """Remembers the discovery baseline and enrolls each PR exactly once."""

    def __init__(self, root: Path, author: str, repositories: list[str]) -> None:
        self.root, self.author, self.repositories = root, author, sorted(repositories)
        self.path = root / "scheduler.json"
        self._file = JsonFile(self.path)
        self.data = read_state(self._file, {"version": 1, "initialized": False, "prs": {},
                                            "author": author, "repositories": self.repositories})
        if not isinstance(self.data.get("prs"), dict):
            raise Blocked(f"Invalid scheduler registry: {self.path}")
        if self.data.get("author") != author or self.data.get("repositories") != self.repositories:
            raise Blocked("Discovery configuration changed; choose a new state_dir or migrate its baseline")

    def enroll(self, prs: list[tuple[str, PullRequest]], include_existing: bool) -> None:
        """Commits a complete baseline, then durably enrolls future discoveries."""
        for repo, pr in prs:
            key = f"{repo}#{pr.number}"
            if key in self.data["prs"]:
                continue
            status = "queued" if self.data["initialized"] or include_existing else "ignored"
            # PR state is created first; replay safely repairs a crash before registry publication.
            state = PrState(self.root, repo, pr.number)
            if not state.path.exists():
                state.status(status)
            self.data["prs"][key] = {"repo": repo, "number": pr.number, "url": pr.url}
        self.data["initialized"] = True
        self._file.write(self.data)

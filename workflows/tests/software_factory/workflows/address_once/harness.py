"""A pull request on the shared `gh` fake, a real temporary repository with a local bare remote, a scripted address agent and a temporary ledger, driven through the address_once entry point."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from loop import (
    Event,
    FakeCliRunner,
    GitCli,
    GitRuntime,
    RecordingSpans,
    RunStatus,
    Services,
    WorktreeGit,
    fake_agent,
)
from loop.shared.git import GitClient

from tests.runtime.harness import GIT, command
from workflows.software_factory.events.pull_requests import PullRequestWork
from workflows.software_factory.infrastructure.agents.launcher import AgentLauncher
from workflows.software_factory.workflows.address_once.__main__ import Runtime, main
from workflows.software_factory.workflows.address_once.definition import NAME
from workflows.tests.software_factory.stages.pr_support import (
    NUMBER,
    SLUG,
    AddressAgent,
    answer,
    thread,
)
from workflows.tests.software_factory.stages.watch_support import WatchRig

URL = f"https://github.com/{SLUG}/pull/{NUMBER}"

# Fails like a red build once an agent edited `cart.py`, and passes on the checkout as it was pushed.
FAILS_AFTER_EDIT = "import pathlib, sys; sys.exit(0 if pathlib.Path('cart.py').read_text() == 'cart\\n' else 1)"


class LocalOrigin(GitClient):
    """Git whose checkout counts as the pull request's repository, though its origin is a local bare repository."""

    def origin_slug(self, path: Path) -> str:
        """The repository the pull request belongs to."""
        return SLUG


class Addressing(WatchRig):
    """One pull request of the rig's repository addressed once by `main`, with a review thread whose fix edits `cart.py`."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        threads: list[dict] | None = None,
        test: str | None = None,
        local_origin: bool = True,
    ) -> None:
        super().__init__(
            tmp_path,
            threads=[thread("t1", "Handle the empty cart")]
            if threads is None
            else threads,
        )
        self.config = tmp_path / "address_once.json"
        self.config.write_text(
            json.dumps(
                {
                    "repositories": [
                        {
                            "name": SLUG,
                            "path": str(self.repo.path),
                            "test": [sys.executable, "-c", test or "pass"],
                        }
                    ],
                    "state_dir": str(tmp_path),
                }
            )
        )
        self.agent = AddressAgent({"i1": answer("fix-needed", edit="cart.py")})
        self.git_client: GitClient = (
            LocalOrigin(self.runner) if local_origin else GitClient(self.runner)
        )
        self.pr = PullRequestWork(self.target, NUMBER, "pull-request")
        self.spans = RecordingSpans()
        self.services = self.build_services()

    def build_services(self) -> Services:
        """The services the entry point takes in place of the real ones."""
        runner = FakeCliRunner(self.agent, speed=0.0)
        launcher = AgentLauncher(
            lambda: fake_agent(runner, GitRuntime(GitCli())), None, (runner,)
        )
        return (
            Services(self.github, launcher, Runtime(self.ledger, self.spans))
            .bind(GitClient, self.git_client)
            .bind(WorktreeGit, GIT)
        )

    def address(self, *argv: str) -> int:
        """Addresses the pull request through the command line and returns its exit code."""
        return main(
            ["--config", str(self.config), "--pr", URL, *argv], services=self.services
        )

    def status(self) -> RunStatus | None:
        """How the pull request's latest run stands."""
        return self.ledger.status(NAME, self.pr.id)

    def history[T: Event](self, event: type[T]) -> tuple[T, ...]:
        """Every `event` the pull request's runs recorded."""
        return self.ledger.item(NAME, self.pr.id).history(event)

    def commits(self) -> list[str]:
        """The subjects the recovery branch added to the pull request's head, oldest first."""
        return command(
            (
                "log",
                "--reverse",
                "--format=%s",
                f"{self.head}..{self.recovery_branch()}",
            ),
            self.repo.path,
        ).splitlines()

    def pushed(self) -> bool:
        """Whether the remote branch moved off the head the pull request started at."""
        return self.remote_head("feature") != self.head

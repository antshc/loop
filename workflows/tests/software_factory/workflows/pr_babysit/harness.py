"""A pull request on the shared `gh` fake, a real temporary repository with a local bare remote, scripted address and maintainer agents and a temporary ledger, driven through the pr_babysit entry point."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loop import (
    AgentRequest,
    Event,
    FakeCliRunner,
    FakeReply,
    GitCli,
    GitRuntime,
    RecordingSpans,
    RunContext,
    RunStatus,
    Services,
    WorktreeGit,
    fake_agent,
)
from loop.shared.git import GitClient

from tests.runtime.harness import GIT, command
from workflows.software_factory.events.pull_requests import PullRequestWork
from workflows.software_factory.infrastructure.agents.launcher import AgentLauncher
from workflows.software_factory.infrastructure.pacing import Pacer
from workflows.software_factory.workflows.pr_babysit.__main__ import Runtime, main
from workflows.software_factory.workflows.pr_babysit.definition import NAME
from workflows.software_factory.workflows.pr_babysit.intake import REVISION
from workflows.tests.software_factory.infrastructure.github.fakes import workflow_run
from workflows.tests.software_factory.stages.pr_support import (
    NUMBER,
    SLUG,
    AddressAgent,
)
from workflows.tests.software_factory.stages.watch_support import (
    MaintainerAgent,
    WatchRig,
)

URL = f"https://github.com/{SLUG}/pull/{NUMBER}"

# Fails like a red build once an agent edited `cart.py`, and passes on the checkout as it was pushed.
FAILS_AFTER_EDIT = "import pathlib, sys; sys.exit(0 if pathlib.Path('cart.py').read_text() == 'cart\\n' else 1)"

# Fails like a red baseline until a CI fix wrote `fix.txt`.
FAILS_UNTIL_FIXED = (
    "import pathlib, sys; sys.exit(0 if pathlib.Path('fix.txt').exists() else 1)"
)


class LocalOrigin(GitClient):
    """Git whose checkout counts as the pull request's repository, though its origin is a local bare repository."""

    def origin_slug(self, path: Path) -> str:
        """The repository the pull request belongs to."""
        return SLUG


class NeverStop:
    """The interrupt signal that never fires."""

    def requested(self) -> bool:
        """Whether the run should stop."""
        return False


class Agents:
    """Answers the address and maintainer agents' requests; each remembers its own, and `modes` lists them all in order."""

    def __init__(self, address: AddressAgent, maintainer: MaintainerAgent) -> None:
        self.address = address
        self.maintainer = maintainer
        self.modes: list[str] = []

    def __call__(self, request: AgentRequest, context: RunContext) -> FakeReply:
        mode = json.loads(request.prompt.rsplit("\n", 1)[-1])["mode"]
        self.modes.append(mode)
        return (self.address if mode in ("group", "resolve") else self.maintainer)(
            request, context
        )


class Babysit(WatchRig):
    """One pull request of the rig's repository maintained by `main`; the pacer runs `on_wait`, which closes the pull request unless told otherwise."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        threads: list[dict] | None = None,
        copilot_review: bool = False,
        build: str | None = None,
        local_origin: bool = True,
        retries: int = 3,
    ) -> None:
        super().__init__(tmp_path, threads=threads)
        self.tmp_path = tmp_path
        self.config = tmp_path / "babysit.json"
        self.config.write_text(
            json.dumps(
                {
                    "repositories": [
                        {
                            "name": SLUG,
                            "path": str(self.repo.path),
                            "build": [sys.executable, "-c", build or "pass"],
                            "test": [sys.executable, "-c", "pass"],
                        }
                    ],
                    "state_dir": str(tmp_path),
                    "interval": 0.01,
                    "retries": retries,
                    "copilot_review": copilot_review,
                }
            )
        )
        self.agent = Agents(AddressAgent({}), MaintainerAgent())
        self.on_wait: Callable[[float], None] = lambda seconds: self.pull.close()
        self.stop: Any = NeverStop()
        self.git_client: GitClient = (
            LocalOrigin(self.runner) if local_origin else GitClient(self.runner)
        )
        self.pr = PullRequestWork(self.target, NUMBER, REVISION)
        self.restart()

    def restart(self) -> None:
        """Starts the next process: fresh spans, and the services over the same repository, host and ledger."""
        self.spans = RecordingSpans()
        self.services = self.build_services()

    def build_services(self) -> Services:
        """The services the entry point takes in place of the real ones."""
        runner = FakeCliRunner(self.agent, speed=0.0)
        launcher = AgentLauncher(
            lambda: fake_agent(runner, GitRuntime(GitCli())), None, (runner,)
        )
        return (
            Services(
                self.github,
                launcher,
                Pacer(lambda seconds: self.on_wait(seconds)),
                Runtime(self.ledger, self.spans, self.stop),
            )
            .bind(GitClient, self.git_client)
            .bind(WorktreeGit, GIT)
        )

    def fail_ci_until_fixed(self) -> None:
        """Makes the head's CI fail until a commit is pushed."""
        self.pull.workflow_runs = lambda: (
            []
            if self.pushed()
            else [
                workflow_run(10, self.head_now(), conclusion="failure", workflow_id=10)
            ]
        )

    def close_after(self, waits: int) -> None:
        """Closes the pull request once the pacer has been asked to wait `waits` times."""

        def on_wait(seconds: float) -> None:
            self.waits.append(seconds)
            if len(self.waits) >= waits:
                self.pull.close()

        self.on_wait = on_wait

    def babysit(self, *argv: str) -> int:
        """Maintains the pull request through the command line and returns its exit code."""
        return main(
            ["--config", str(self.config), "--pr", URL, *argv], services=self.services
        )

    def discover(self, *argv: str, services: Services | None = None) -> int:
        """Runs discovery through the command line over `services`."""
        return main(
            ["--config", str(self.config), *argv], services=services or self.services
        )

    def status(self) -> RunStatus | None:
        """How the pull request's latest run stands."""
        return self.ledger.status(NAME, self.pr.id)

    def kinds(self) -> list[str]:
        """The kind of every ledger record, in order."""
        return [
            record["kind"] for record in self.ledger.item(NAME, self.pr.id).records()
        ]

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

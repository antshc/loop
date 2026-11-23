"""Demo: one Spec through plan, deliver (one rework), chore, review and finalize, on fakes."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from ..composition import bind
from ..host import AgentClient
from ..integrations import WorktreeGit
from . import ImplementSpec, SpecRoute, Tracker, create_implement_spec_host
from .fakes import FakeGit, FakeTracker, ScriptedFactoryAgent

SPEC = 1


async def main() -> None:
    """Runs the Workflow and prints one prompt per role, the calls, the commits and the comments."""
    git = FakeGit()
    tracker = FakeTracker(SPEC, "Basket: Discount policy", "Give 10% off over 100.")
    agent = ScriptedFactoryAgent(git, fail_first={11})
    host = create_implement_spec_host(
        bind(Tracker, tracker), bind(WorktreeGit, git), bind(AgentClient, agent)
    )
    with tempfile.TemporaryDirectory(prefix="agentmw-") as root:
        route = _route(Path(root))
        try:
            outcome = await ImplementSpec(host, tracker, git).run(route)
        finally:
            await host.aclose()
        commits = git.log(route.worktree)
    for role in ("planner", "developer", "chore", "reviewer"):
        print(f"===== first {role} prompt =====\n{agent.prompts(role)[0]}")
    print("calls:", " -> ".join(role for role, _ in agent.calls))
    print("commits:", *(f"  {c.sha[:7]} {c.subject}" for c in commits), sep="\n")
    print("Spec comments:", *(f"  {c}" for c in tracker.comments(SPEC)), sep="\n")
    print(f"outcome: {outcome}")


def _route(root: Path) -> SpecRoute:
    checkout, worktree = root / "checkout", root / "worktree"
    checkout.mkdir()
    worktree.mkdir()
    return SpecRoute(
        SPEC, "basket-discount", "main", "basket-discount", str(checkout), str(worktree)
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    asyncio.run(main())

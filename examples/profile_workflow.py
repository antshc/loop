from __future__ import annotations

from examples.agents.developer import developer
from loop import Agent, AgentOptions, AgentRequest, BranchStrategy, GitOptions


def main() -> None:
    """Demonstrate imported profiles directly and on a shared worktree in dry-run mode."""
    request = AgentRequest("Add shopping-cart quantity validation and verify it.")
    result = Agent(AgentOptions(dry_run=True)).create(developer).run(request)
    print(result.output)

    git = GitOptions(strategy=BranchStrategy("loop/cart-validation"))
    with Agent(AgentOptions(dry_run=True)).with_git(git).open() as worktree:
        result = worktree.agent(developer).run(request)
        print(result.output)


if __name__ == "__main__":
    main()

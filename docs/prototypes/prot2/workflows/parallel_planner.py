"""Plan -> implement + review in parallel -> merge. Port of Sandcastle's .sandcastle/run.ts."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from orb import (
    AgentClient,
    Hook,
    Hooks,
    SandboxProvider,
    copilot,
    create_sandbox,
    extract_json,
    parallel_settled,
    run,
    worktree,
)

PROMPTS = Path(__file__).parent / "prompts"
LIST_ISSUES = (
    "gh issue list --state open --label ready-for-agent --limit 100 "
    "--json number,title,body,labels,comments"
)
logger = logging.getLogger("workflow.parallel_planner")


def main(
    argv: list[str] | None = None,
    *,
    agent: AgentClient | None = None,
    sandbox: SandboxProvider | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="parallel_planner")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-iterations", type=int, default=10)
    parser.add_argument("--max-parallel", type=int, default=4)
    parser.add_argument("--setup", default=None, help="shell command run once in each new worktree")
    parser.add_argument("--list-issues-command", default=LIST_ISSUES)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")

    repo = args.repo.resolve()
    provider = sandbox or worktree()
    ai = agent or copilot(args.model, add_dirs=(repo,))
    hooks = Hooks(on_sandbox_ready=(Hook(args.setup),) if args.setup else ())
    failed = False

    for iteration in range(1, args.max_iterations + 1):
        logger.info("=== Iteration %d/%d ===", iteration, args.max_iterations)

        plan = run(
            sandbox=provider,
            agent=ai,
            repo=repo,
            name="Planner",
            merge_to_host=False,
            prompt_file=PROMPTS / "plan.md",
            prompt_args={"LIST_ISSUES_COMMAND": args.list_issues_command},
        )
        issues = extract_json(plan.stdout, "plan")["issues"]
        if not issues:
            logger.info("No issues to work on.")
            break

        def implement(issue: dict) -> tuple[str, ...]:
            prompt_args = {
                "TASK_ID": str(issue["number"]),
                "ISSUE_TITLE": issue["title"],
                "BRANCH": issue["branch"],
            }
            with create_sandbox(sandbox=provider, repo=repo, branch=issue["branch"], hooks=hooks) as box:
                result = box.run(
                    agent=ai,
                    name=f"Implementer #{issue['number']}",
                    prompt_file=PROMPTS / "implement.md",
                    prompt_args=prompt_args,
                )
                if result.commits:
                    box.run(
                        agent=ai,
                        name=f"Reviewer #{issue['number']}",
                        prompt_file=PROMPTS / "review.md",
                        prompt_args=prompt_args,
                    )
                return result.commits

        settled = parallel_settled(issues, implement, max_parallel=args.max_parallel)
        merged = []
        for issue, outcome in zip(issues, settled):
            if outcome.error is not None:
                failed = True
                logger.error("#%s (%s) failed: %s", issue["number"], issue["branch"], outcome.error)
            elif outcome.value:
                merged.append(issue)
        if not merged:
            logger.info("No commits produced. Nothing to merge.")
            continue

        run(
            sandbox=provider,
            agent=ai,
            repo=repo,
            name="Merger",
            max_iterations=10,
            prompt_file=PROMPTS / "merge.md",
            prompt_args={
                "BRANCHES": "\n".join(f"- {issue['branch']}" for issue in merged),
                "ISSUES": "\n".join(f"- #{issue['number']}: {issue['title']}" for issue in merged),
            },
        )
        logger.info("Branches merged.")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

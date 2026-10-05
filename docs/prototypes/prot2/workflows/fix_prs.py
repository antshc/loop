"""Apply each open review thread's requested change on the pull request branch."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from orb import (
    AgentClient,
    ExecutionStore,
    FileExecutionStore,
    PlatformAdapter,
    SandboxProvider,
    OrbError,
    copilot,
    create_sandbox,
    may_attempt,
    platform_for_repo,
    worktree,
)

PROMPT = Path(__file__).parent / "prompts" / "fix.md"
logger = logging.getLogger("workflow.fix_prs")


def main(
    argv: list[str] | None = None,
    *,
    agent: AgentClient | None = None,
    sandbox: SandboxProvider | None = None,
    platform: PlatformAdapter | None = None,
    store: ExecutionStore | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="fix_prs")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--model", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-dir", type=Path, default=Path(".orb-logs"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")

    repo = args.repo.resolve()
    tracker = platform or platform_for_repo(repo, dry_run=args.dry_run)
    attempts = store or FileExecutionStore(args.log_dir)
    provider = sandbox or worktree()
    ai = agent or copilot(args.model, add_dirs=(repo,))
    failed = False

    for pull_request in tracker.list_pull_requests():
        threads = [t for t in tracker.review_threads(pull_request.id) if not t.resolved]
        if not threads:
            continue
        with create_sandbox(sandbox=provider, repo=repo, branch=pull_request.branch) as box:
            for thread in threads:
                key = f"fix-prs:{pull_request.id}:{thread.id}"
                if not may_attempt(attempts, key):
                    logger.warning("skip %s: attempt cap reached", key)
                    continue
                try:
                    result = box.run(
                        agent=ai,
                        name=f"Fix PR {pull_request.id}",
                        prompt_file=PROMPT,
                        prompt_args={
                            "PR_TITLE": pull_request.title,
                            "PATH": thread.path,
                            "COMMENT": thread.body,
                        },
                    )
                    success = bool(result.commits)
                except OrbError as exception:
                    logger.error("%s: %s", key, exception)
                    success = False
                attempts.record(key, success)
                logger.info("%s -> %s", key, "ok" if success else "failed")
                failed = failed or not success
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

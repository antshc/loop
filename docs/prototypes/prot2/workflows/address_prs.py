"""Draft a reply for each open review thread and post it."""

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
    CapsuleProvider,
    OrbError,
    copilot,
    may_attempt,
    platform_for_repo,
    run,
    worktree,
)

PROMPT = Path(__file__).parent / "prompts" / "address.md"
logger = logging.getLogger("workflow.address_prs")


def main(
    argv: list[str] | None = None,
    *,
    agent: AgentClient | None = None,
    capsule: CapsuleProvider | None = None,
    platform: PlatformAdapter | None = None,
    store: ExecutionStore | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="address_prs")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--model", default=None)
    parser.add_argument("--review", default=None, help="only this pull request id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-dir", type=Path, default=Path(".orb-logs"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")

    repo = args.repo.resolve()
    tracker = platform or platform_for_repo(repo, dry_run=args.dry_run)
    attempts = store or FileExecutionStore(args.log_dir)
    provider = capsule or worktree()
    ai = agent or copilot(args.model, add_dirs=(repo,))
    failed = False

    for pull_request in tracker.list_pull_requests():
        if args.review is not None and pull_request.id != args.review:
            continue
        for thread in tracker.review_threads(pull_request.id):
            if thread.resolved:
                continue
            key = f"address-prs:{pull_request.id}:{thread.id}"
            if not may_attempt(attempts, key):
                logger.warning("skip %s: attempt cap reached", key)
                continue
            try:
                result = run(
                    capsule=provider,
                    agent=ai,
                    repo=repo,
                    name=f"Address PR {pull_request.id}",
                    merge_to_host=False,
                    prompt_file=PROMPT,
                    prompt_args={
                        "PR_TITLE": pull_request.title,
                        "PATH": thread.path,
                        "COMMENT": thread.body,
                    },
                )
                tracker.reply_to_thread(pull_request.id, thread.id, result.stdout.strip())
                success = True
            except OrbError as exception:
                logger.error("%s: %s", key, exception)
                success = False
            attempts.record(key, success)
            logger.info("%s -> %s", key, "ok" if success else "failed")
            failed = failed or not success
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

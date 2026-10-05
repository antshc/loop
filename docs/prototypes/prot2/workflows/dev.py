"""Autonomous dev loop: one sandbox branch per open spec, capped retries."""

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
    may_attempt,
    platform_for_repo,
    run,
    worktree,
)

PROMPT = Path(__file__).parent / "prompts" / "dev.md"
logger = logging.getLogger("workflow.dev")


def main(
    argv: list[str] | None = None,
    *,
    agent: AgentClient | None = None,
    sandbox: SandboxProvider | None = None,
    platform: PlatformAdapter | None = None,
    store: ExecutionStore | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="dev")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--model", default=None)
    parser.add_argument("--limit", type=int, default=1, help="max specs per run")
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

    for spec in tracker.list_specs()[: args.limit]:
        key = f"dev:{spec.id}"
        if not may_attempt(attempts, key):
            logger.warning("skip %s: attempt cap reached", key)
            continue
        try:
            result = run(
                sandbox=provider,
                agent=ai,
                repo=repo,
                branch=f"orb/spec-{spec.id}",
                name=f"Dev #{spec.id}",
                prompt_file=PROMPT,
                prompt_args={"SPEC_ID": spec.id, "SPEC_TITLE": spec.title, "SPEC_URL": spec.url},
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

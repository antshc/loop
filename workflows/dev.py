"""Autonomous dev loop: one branch, worktree, and capsule per open spec, capped retries."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path

from orb import (
    DEFAULT_COMPLETION_SIGNAL,
    AgentClientFactory,
    AgentError,
    AgentOptions,
    Capsule,
    DockerCapsule,
    ExecutionStore,
    FileExecutionStore,
    FileSessionStore,
    GitClient,
    GitHubClient,
    NoCapsule,
    OrbError,
    SessionStore,
    Spec,
    copilot,
    may_attempt,
)

PROMPT = Path(__file__).parent / "prompts" / "dev.md"
SESSION_PREFIX = "orb-"
logger = logging.getLogger("workflow.dev")

CapsuleFactory = Callable[[Path, AgentClientFactory], Capsule]


def main(
    argv: list[str] | None = None,
    *,
    agent_factory: AgentClientFactory | None = None,
    capsule_factory: CapsuleFactory | None = None,
    git: GitClient | None = None,
    github: GitHubClient | None = None,
    store: ExecutionStore | None = None,
    sessions: SessionStore | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="dev")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--model", default=None)
    parser.add_argument("--limit", type=int, default=1, help="max specs per run")
    parser.add_argument("--max-iterations", type=int, default=1, help="agent runs per spec")
    parser.add_argument("--capsule", choices=("none", "docker"), default="none")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-dir", type=Path, default=Path(".orb-logs"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")

    repo = args.repo.resolve()
    git = git or GitClient()
    github = github or GitHubClient.for_repo(repo, dry_run=args.dry_run)[0]
    attempts = store or FileExecutionStore(args.log_dir)
    agent_factory = agent_factory or copilot(sessions or FileSessionStore(args.log_dir))
    capsule_factory = capsule_factory or (
        DockerCapsule if args.capsule == "docker" else NoCapsule
    )
    # A host agent needs the main checkout's .git, which sits outside its worktree.
    add_dirs = (repo,) if args.capsule == "none" else ()
    template = PROMPT.read_text()
    failed = False

    for spec in github.get_specs()[: args.limit]:
        key = f"dev:{spec.number}"
        if not may_attempt(attempts, key):
            logger.warning("skip %s: attempt cap reached", key)
            continue
        options = AgentOptions(
            model=args.model,
            session_key=f"dev-{spec.number}",
            session_name_prefix=SESSION_PREFIX,
            add_dirs=add_dirs,
        )
        try:
            success = _develop(spec, template, options, args.max_iterations, repo, git, capsule_factory, agent_factory)
        except OrbError as exception:
            logger.error("%s: %s", key, exception)
            success = False
        attempts.record(key, success)
        logger.info("%s -> %s", key, "ok" if success else "failed")
        failed = failed or not success
    return 1 if failed else 0


def _develop(
    spec: Spec,
    template: str,
    options: AgentOptions,
    max_iterations: int,
    repo: Path,
    git: GitClient,
    capsule_factory: CapsuleFactory,
    agent_factory: AgentClientFactory,
) -> bool:
    """Ralph loop on the spec's branch; success means the agent left changes to commit."""
    branch = f"orb/spec-{spec.number}"
    # TODO(#40): resolve the real target branch instead of assuming "main".
    workspace = git.create_worktree(repo, branch, "main")
    prompt_args = {
        "SOURCE_BRANCH": branch,
        "SPEC_ID": str(spec.number),
        "SPEC_TITLE": spec.title,
        "SPEC_URL": spec.url,
    }
    try:
        with capsule_factory(workspace, agent_factory) as capsule:
            for iteration in range(1, max_iterations + 1):
                logger.info("[Dev #%s] iteration %d/%d on %s", spec.number, iteration, max_iterations, branch)
                result = capsule.run(template, prompt_args, options)
                if not result.success:
                    raise AgentError(f"Dev #{spec.number}", result.output)
                if DEFAULT_COMPLETION_SIGNAL in result.stdout:
                    break
        return git.has_changes(workspace)
    finally:
        git.remove_worktree(workspace)


if __name__ == "__main__":
    sys.exit(main())

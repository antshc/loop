"""Address use case: lists open PRs and drives the Copilot agent to answer review discussions."""

from pathlib import Path

from afk.infrastructure.ai_agent import AIAgent
from modules.github.infrastructure.vcs_client import VCSClient
from modules.github.pr_discussion_state import build_state
import logging

from afk.shared.execution_log import ExecutionLog

_log = logging.getLogger(__name__)


def address_prs(
    github_user: str,
    github_repo: str,
    log_dir: Path,
    max_executions: int,
    prompt: str = "/ralph:address",
    *,
    agent_name: str = "yolo",
    vcs: VCSClient | None = None,
    agent: AIAgent | None = None,
    exec_log: ExecutionLog | None = None,
) -> None:
    """List open PRs for *github_user* and run the Copilot agent on actionable review discussions.

    Args:
        github_user:    GitHub username; only PRs authored by this user are processed.
        github_repo:    Repository in owner/repo format.
        log_dir:        Directory where execution logs are written.
        max_executions: Maximum processing attempts per PR before skipping.
        prompt:         Prompt text passed to the AI agent (default: "/ralph:address").
        vcs:            VCSClient instance (defaults to VCSClient()).
        agent:          AIAgent instance (defaults to AIAgent()).
        exec_log:       ExecutionLog instance (defaults to ExecutionLog(log_dir, github_repo, "address-prs")).
    """
    exec_log = exec_log or ExecutionLog(log_dir, github_repo, "address-prs")
    vcs = vcs or VCSClient()

    _log.info("Service run started", extra={"user": github_user, "repo": github_repo})

    pull_requests = vcs.list_prs(github_user, github_repo)

    if not pull_requests:
        _log.info("No open PRs found for user", extra={"user": github_user, "repo": github_repo})
        return

    _log.info("Found open PRs", extra={"count": len(pull_requests), "user": github_user})

    for pr in pull_requests:
        _log.info("Processing PR", extra={"pr_url": pr.url})

        exec_count = exec_log.get_count(pr.url)

        state, _ = build_state(pr.url)
        thread_ids = [thread["id"] for thread in state.get("threads", [])]

        if state.get("action") != "proceed":
            _log.info("No actionable threads, skipping", extra={"pr_url": pr.url})
            if exec_count > 0:
                exec_log.reset(pr.url)
                _log.info("Reset execution count (all threads resolved)", extra={"pr_url": pr.url})
            continue

        if exec_count >= max_executions:
            _log.warning(
                "PR exceeded max executions, skipping",
                extra={"pr_url": pr.url, "count": exec_count, "max": max_executions, "unresolved_threads": len(thread_ids)},
            )
            continue

        _log.info(
            "Running copilot agent",
            extra={"pr_url": pr.url, "threads": len(thread_ids), "attempt": exec_count + 1},
        )

        (agent or AIAgent(alias=agent_name, prompt=prompt)).run()

        exec_log.update(pr.url, thread_ids, pr.owner, pr.repo, "pull_request", pr.number, pr.title)
        _log.info("Completed PR processing", extra={"pr_url": pr.url, "attempt": exec_count + 1})

    _log.info("Service run completed")

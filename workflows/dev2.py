"""Reads the open Specs and delivers their Tickets in a worktree, one fresh agent run per Ticket."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from loop import (
    DEFAULT,
    Agent,
    AgentBuilder,
    AgentClient,
    AgentRequest,
    BranchStrategy,
    GitOptions,
    LoopHook,
    Prompt,
    PromptError,
    configure_logging,
)
from workflows.dev import (
    LOOP_HOOKS,
    MAX_TICKET_FAILURES,
    DevError,
    DevResponse,
    DevResult,
    FileExecutionStore,
    prompt_args,
)
from workflows.platforms.git import Commit, GitService
from workflows.platforms.prompt_source import FilePromptSource, PromptSource
from workflows.platforms.work_tracking import (
    GhCli,
    Spec,
    Ticket,
    TicketsTracker,
    AgentCommitSubject,
)

__all__ = ["main", "read_specs"]

logger = logging.getLogger("workflow.dev2")

LOG_DIR_NAME = ".loop"
LOG_LEVEL = "INFO"
_HARNESS_PATH = Path(__file__).resolve().parents[1]
_WORKTREE_ROOT = _HARNESS_PATH / "workspace" / f"{_HARNESS_PATH.name}.worktrees"
_PROMPTS_DIR = Path(__file__).parent / "prompts"
OWNER = "antshc"
REPO = "loop"


def read_specs(tracker: TicketsTracker) -> list[Spec]:
    """Every open Spec with its Tickets, as the tracker reads them."""
    return list(tracker.specs())


def _initiative_commits(
    git: GitService, worktree: Path, spec: Spec, identifier: AgentCommitSubject
) -> list[str]:
    """This Initiative's `ccode(<initiative-id>|` commits on the feature branch since the base branch, as `<short hash> <subject>`."""
    found = git.initiative_commits(worktree, spec.base_branch, identifier.initiative_commit_prefix)
    return [f"{commit.sha[:7]} {commit.subject}" for commit in found]


def _dev_prompt(
    template: str,
    ticket: Ticket,
    identifier: AgentCommitSubject,
    initiative_commits: list[str],
    worktree: Path,
    spec: Spec,
) -> Prompt:
    """The dev prompt for one Ticket run."""
    args = prompt_args(ticket, identifier, initiative_commits, worktree, spec.base_branch, spec.feature_branch)
    return Prompt(template, args)


def _attempt(
    spec: Spec,
    ticket: Ticket,
    worktree: Path,
    client: AgentClient,
    head_before: Commit,
    *,
    git: GitService,
    template: str,
) -> DevResult | str:
    """Runs the agent once and validates its response and Git; returns the accepted result, or the failure reason."""
    try:
        identifier = AgentCommitSubject(spec.initiative, ticket.number)
        prompt = _dev_prompt(
            template,
            ticket,
            identifier,
            _initiative_commits(git, worktree, spec, identifier),
            worktree,
            spec,
        )
        output = client.run(AgentRequest(prompt.render())).output
        dev_result = DevResponse(identifier).decode(output)
    except subprocess.CalledProcessError:
        return "agent process did not exit successfully"
    except (DevError, PromptError) as exception:
        return str(exception)
    if dev_result.status == "failed":
        return dev_result.reason or ""

    violation = _commit_violation(
        git, worktree, head_before, identifier, dev_result
    )
    return dev_result if violation is None else violation


def _commit_violation(
    git: GitService, worktree: Path, head_before: Commit, identifier: AgentCommitSubject, dev_result: DevResult
) -> str | None:
    """Why the worktree does not hold exactly the one clean, correctly tagged commit the result claims, or None."""
    head = git.head(worktree)
    if head.sha == head_before.sha:
        return "HEAD did not change: the agent made no commit"
    new_commits = git.commits_since(worktree, head_before.sha)
    if len(new_commits) != 1:
        return f"expected exactly one commit since {head_before.sha}, found {len(new_commits)}"
    prefix = identifier.to_subject()
    if not head.subject.startswith(prefix):
        return f"HEAD subject {head.subject!r} does not start with {prefix!r}"
    if not git.is_clean(worktree):
        return "the worktree has uncommitted changes"
    if dev_result.commit != head.sha:
        return f"result.commit {dev_result.commit!r} does not equal HEAD {head.sha!r}"
    return None


def _record_failure(store: FileExecutionStore, harness_owner_repo: str, ticket: Ticket) -> int:
    """Counts one failure for `ticket` and returns its persisted total."""
    owner, repo = harness_owner_repo.split("/", 1)
    store.record_failure(
        ticket.url,
        owner=owner,
        repo=repo,
        task_id=str(ticket.number),
        title=ticket.title,
        items=[ticket.number],
    )
    return store.failed_attempts(ticket.url)


def _log_spec(spec: Spec) -> None:
    logger.info(
        "spec #%s %r awaiting_human=%s has_work=%s target=%s base=%s",
        spec.number,
        spec.title,
        spec.awaiting_human,
        spec.has_work,
        spec.target,
        spec.base_branch,
    )
    for ticket in spec.tickets:
        logger.info("  ticket #%s %r state=%s actionable=%s", ticket.number, ticket.title, ticket.state, ticket.actionable)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dev2")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    git: GitService | None = None,
    new_agent: Callable[[], AgentBuilder] = Agent,
    store: FileExecutionStore | None = None,
    prompt_source: PromptSource = FilePromptSource(_PROMPTS_DIR),
    loop_hooks: tuple[LoopHook, ...] = LOOP_HOOKS,
) -> int:
    """Delivers the Tickets of every open Spec once; returns the process exit code."""
    args = _parse_args(argv)

    log_dir = (args.log_dir or Path.cwd() / LOG_DIR_NAME).resolve()
    configure_logging(args.log_level, log_file=log_dir / "dev2.log", logger="")

    tracker = TicketsTracker(OWNER, REPO, gh=GhCli(cwd=_HARNESS_PATH))
    git = git or GitService()
    store = store or FileExecutionStore(log_dir)
    template = prompt_source.load("dev")

    failed = False
    try:
        for spec in read_specs(tracker):
            _log_spec(spec)
            if spec.awaiting_human or not spec.has_work:
                continue
            # Missing: hand the Spec to a human when its repo:target/repo:base labels do not resolve.
            if spec.base_branch is None or spec.feature_branch is None:
                logger.warning("spec #%s skipped: no repo:base label", spec.number)
                continue
            # Missing: harness target validation, git fetch, and the base-branch existence check.
            options = GitOptions(
                root_path=_WORKTREE_ROOT,
                repository_path=_HARNESS_PATH,
                strategy=BranchStrategy(spec.feature_branch, f"origin/{spec.base_branch}"),
                loop_hooks=loop_hooks,
            )
            with new_agent().with_git(options).open() as agent_workspace:
                client = agent_workspace.agent(DEFAULT)
                delivered = True
                # Each actionable Ticket in order; stops at the first one that cannot be delivered.
                for ticket in spec.tickets:
                    delivered = False
                    # Fresh agent runs until one is accepted or the Ticket's failure cap is reached.
                    while True:
                        head_before = git.head(agent_workspace.path)
                        attempt = _attempt(
                            spec, ticket, agent_workspace.path, client, head_before, git=git, template=template
                        )
                        if isinstance(attempt, DevResult):
                            spec.close_ticket(
                                ticket.number,
                                f"Delivered in {attempt.commit}.\n\n{attempt.summary}\n\n{attempt.verification}",
                            )
                            tracker.update_spec(spec)
                            store.reset(ticket.url)
                            delivered = True
                            break
                        git.restore(agent_workspace.path, head_before.sha)
                        if _record_failure(store, f"{OWNER}/{REPO}", ticket) >= MAX_TICKET_FAILURES:
                            spec.escalate(ticket.number, attempt)
                            tracker.update_spec(spec)
                            break
                    if not delivered:
                        break
                # Missing: push the feature branch, publish the draft pull request, and announce the delivery on the Spec.
            failed = failed or not delivered
    except Exception as exception:  # last-resort boundary: log and fail, never crash bare
        logger.exception("unexpected error: %s", exception)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

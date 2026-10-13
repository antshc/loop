"""Routes harness Specs to repository-specific agent workflows on one worktree per Spec."""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum, auto
from functools import partial
from pathlib import Path
from typing import Any

from loop import (
    DEFAULT,
    Agent,
    AgentBuilder,
    AgentRequest,
    AgentWorkspace,
    BranchStrategy,
    GitOptions,
    LoopHook,
    LoopHookError,
    configure_logging,
)
from workflows.content import ContentDecodeError, LastJsonObject
from workflows.platforms.git import GitService
from workflows.platforms.process import CommandError
from workflows.platforms.work_tracking import (
    GhCli,
    GitHubRepo,
    PullRequestClient,
    Repository,
    RepositoryId,
    Spec,
    Ticket,
    TicketsTracker,
    AgentCommitSubject,
)
from workflows.tickets import (
    AgentWorkflow,
    ExecutionStoreError,
    FileExecutionStore,
    TicketContext,
    TicketResult as DevResult,
    TicketWorkflowError as DevError,
    deliver_tickets,
)

__all__ = [
    "HARNESS",
    "PROMPT",
    "DevResponse",
    "DevResult",
    "DevResultError",
    "ExecutionStoreError",
    "FileExecutionStore",
    "RepositoryWorkflow",
    "implement_ticket",
    "main",
    "parse_dev_result",
    "prompt_args",
]

logger = logging.getLogger("workflow.dev")

# --- Settings: --log-dir and --log-level are the only CLI overrides ---

LOG_DIR_NAME = ".loop"
LOG_LEVEL = "INFO"
LOOP_HOOKS: tuple[LoopHook, ...] = ()
_ROOT = Path(__file__).resolve().parents[1]
# Specs and Tickets stay here even when their code targets another repository.
HARNESS: Repository = Repository(
    checkout=_ROOT,
    worktrees=_ROOT / "workspace" / f"{_ROOT.name}.worktrees",
    owner_name=RepositoryId("antshc", "loop"),
)
MAX_TICKET_FAILURES = 2
PROMPT = Path(__file__).parent / "prompts" / "dev.md"

# --- Errors the dev workflow raises on purpose ---


class PromptError(DevError):
    """A prompt template placeholder has no matching argument."""


class DevResultError(DevError):
    """The agent's response is missing, not valid JSON, or missing a required `result` field."""


# --- The prompt template's arguments for one Ticket run, and their substitution ---

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def render_prompt(template: str, args: Mapping[str, str]) -> str:
    """Replaces each `{{KEY}}` placeholder; a missing argument raises, an unused one is logged."""
    used: set[str] = set()

    def substitute(match: re.Match[str]) -> str:
        key = match[1]
        if key not in args:
            raise PromptError(f"missing prompt argument: {key}")
        used.add(key)
        return args[key]

    rendered = _PLACEHOLDER.sub(substitute, template)
    for key in sorted(set(args) - used):
        logger.warning("unused prompt argument: %s", key)
    return rendered


def prompt_args(
    ticket: Ticket,
    identifier: AgentCommitSubject,
    initiative_commits: Sequence[str],
    worktree: Path,
    base_branch: str,
    feature_branch: str,
) -> dict[str, str]:
    """Values for every placeholder in the dev prompt template."""
    return {
        "TICKET_JSON": json.dumps(asdict(ticket), indent=2),
        "INITIATIVE_COMMITS": "\n".join(initiative_commits) or "No task commits for this Initiative exist on the feature branch yet.",
        "COMMIT_SUBJECT_PREFIX": identifier.to_subject(),
        "WORKTREE_PATH": str(worktree),
        "TARGET_BRANCH": base_branch,
        "FEATURE_BRANCH": feature_branch,
    }


# --- The dev result model: decodes the agent's response envelope ---


def _check_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    identifier, status, result = envelope.get("identifier"), envelope.get("status"), envelope.get("result")
    if not isinstance(identifier, str) or status not in ("completed", "failed") or not isinstance(result, dict):
        raise DevResultError(f"malformed response envelope: {envelope!r}")
    return envelope


def _failed_result(identifier: str, result: dict[str, Any]) -> DevResult:
    reason = result.get("reason")
    if not isinstance(reason, str):
        raise DevResultError(f"failed response is missing result.reason: {result!r}")
    return DevResult(identifier, "failed", reason=reason)


def _completed_result(identifier: str, result: dict[str, Any]) -> DevResult:
    commit, summary, verification = result.get("commit"), result.get("summary"), result.get("verification")
    if not isinstance(commit, str) or not isinstance(summary, str) or not isinstance(verification, str):
        raise DevResultError(f"completed response is missing a required result field: {result!r}")
    return DevResult(identifier, "completed", commit=commit, summary=summary, verification=verification)


def parse_dev_result(envelope: dict[str, Any]) -> DevResult:
    """Decodes a run's response envelope for the `completed` and `failed` shapes `dev` understands."""
    envelope = _check_envelope(envelope)
    identifier, result = envelope["identifier"], envelope["result"]
    if envelope["status"] == "failed":
        return _failed_result(identifier, result)
    return _completed_result(identifier, result)


class DevResponse:
    """Decodes a run's response envelope for `identifier`; call `decode` directly on `AgentResult.output`."""

    def __init__(self, identifier: AgentCommitSubject) -> None:
        self._identifier = identifier
        self._envelope = LastJsonObject(lambda value: "status" in value)

    def decode(self, text: str) -> DevResult:
        """Decodes the last response object in `text` and checks it answers this task."""
        try:
            envelope = self._envelope.decode(text)
        except ContentDecodeError as exception:
            raise DevResultError("no response object in agent output") from exception
        if "identifier" in envelope and envelope["identifier"] != str(self._identifier):
            raise DevResultError(f"response identifier {envelope['identifier']!r} does not match task {self._identifier!r}")
        return parse_dev_result({**envelope, "identifier": str(self._identifier)})


# --- The default agent delegate; other repositories can supply different steps ---


def implement_ticket(context: TicketContext, worktree: AgentWorkspace, *, template: str) -> DevResult:
    """Runs one fresh implementation agent and decodes its response for the selected Ticket."""
    args = prompt_args(
        context.ticket,
        context.identifier,
        context.initiative_commits,
        worktree.path,
        context.base_branch,
        context.feature_branch,
    )
    prompt = render_prompt(template, args)
    output = worktree.agent(DEFAULT).run(AgentRequest(prompt)).output
    return DevResponse(context.identifier).decode(output)


# --- Dependencies `main` wires once and passes through the orchestration ---

GithubFactory = Callable[[Path], GitHubRepo]


@dataclass(frozen=True)
class RepositoryWorkflow:
    """One allowed target repository and the agent steps used to deliver its Tickets."""

    repository: Repository
    run_agents: AgentWorkflow


@dataclass(frozen=True)
class DevDeps:
    """Shared dependencies for routing and delivering harness Specs."""

    tracker: TicketsTracker
    store: FileExecutionStore
    git: GitService
    new_agent: Callable[[], AgentBuilder]
    loop_hooks: tuple[LoopHook, ...]
    ticket_repository: RepositoryId
    github_factory: GithubFactory | None


# --- Spec orchestration: prepare, deliver, publish, and announce each open Spec ---


class Outcome(Enum):
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


def process_specs(repositories: Sequence[RepositoryWorkflow], deps: DevDeps) -> int:
    """Routes every non-human-blocked Spec to its configured repository and returns the exit code."""
    targets: dict[str, RepositoryWorkflow] = {}
    for workflow in repositories:
        target = str(workflow.repository.owner_name).casefold()
        if target in targets:
            raise DevError(f"duplicate repository workflow: {target}")
        targets[target] = workflow
    failed = False
    for spec in deps.tracker.specs():
        if not spec.awaiting_human:
            failed = (_process_spec(spec, targets, deps) is Outcome.FAILED) or failed
    return 1 if failed else 0


def _process_spec(spec: Spec, targets: Mapping[str, RepositoryWorkflow], deps: DevDeps) -> Outcome:
    target = spec.target
    if target is None or spec.base_branch is None:
        spec.block(f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
        deps.tracker.update_spec(spec)
        return Outcome.SKIPPED
    workflow = targets.get(target.casefold())
    if workflow is None:
        spec.block(f"dev: repo:target:{target} is not configured for harness {deps.ticket_repository}")
        deps.tracker.update_spec(spec)
        return Outcome.SKIPPED
    try:
        if not _prepare(spec, workflow.repository, deps):
            return Outcome.SKIPPED
        return _deliver_on_worktree(spec, workflow, deps)
    except (CommandError, LoopHookError, subprocess.CalledProcessError) as exception:
        logger.error("spec #%s failed: %s", spec.number, exception)
        spec.comment(f"dev: {_describe(exception)}")
        deps.tracker.update_spec(spec)
        return Outcome.FAILED


def _prepare(spec: Spec, repository: Repository, deps: DevDeps) -> bool:
    deps.git.fetch(repository.checkout)
    if not spec.has_work:
        _publish_pull_request(spec, repository, repository.checkout, deps)
        return False
    base_branch = spec.base_branch
    if base_branch is None:
        raise DevError(f"cannot resolve base branch for Spec {spec.url}")
    if not deps.git.remote_branch_exists(repository.checkout, base_branch):
        spec.hand_to_human(f"dev: target branch {base_branch!r} does not exist on {spec.target}")
        deps.tracker.update_spec(spec)
        return False
    # Publish earlier runs' commits from the checkout before the worktree opens.
    _publish_pull_request(spec, repository, repository.checkout, deps)
    return True


def _deliver_on_worktree(spec: Spec, workflow: RepositoryWorkflow, deps: DevDeps) -> Outcome:
    feature_branch = spec.feature_branch
    if feature_branch is None:
        raise DevError(f"cannot resolve feature branch for Spec {spec.url}")
    options = GitOptions(
        root_path=workflow.repository.worktrees,
        repository_path=workflow.repository.checkout,
        strategy=BranchStrategy(feature_branch, f"origin/{spec.base_branch}"),
        loop_hooks=deps.loop_hooks,
    )
    with deps.new_agent().with_git(options).open() as worktree:
        delivered = deliver_tickets(
            spec,
            worktree,
            workflow.run_agents,
            git=deps.git,
            tracker=deps.tracker,
            store=deps.store,
            ticket_repository=deps.ticket_repository,
            max_failures=MAX_TICKET_FAILURES,
        )
        pull_request_url = _publish_pull_request(spec, workflow.repository, worktree.path, deps)
        if delivered and pull_request_url is not None:
            spec.announce_delivered(pull_request_url)
            deps.tracker.update_spec(spec)
    return Outcome.SUCCESS if delivered else Outcome.FAILED


def _publish_pull_request(spec: Spec, repository: Repository, pusher: Path, deps: DevDeps) -> str | None:
    base_branch, feature_branch = spec.base_branch, spec.feature_branch
    if base_branch is None or feature_branch is None:
        raise DevError(f"cannot resolve branches for Spec {spec.url}")
    if not deps.git.push_if_ahead(pusher, feature_branch, base_branch):
        return None
    client = PullRequestClient(_github(repository, deps.github_factory))
    title = f"{spec.initiative}: {spec.bare_title}"
    return client.create_draft_pull_request(feature_branch, base_branch, title).url


def _describe(exception: Exception) -> str:
    if isinstance(exception, subprocess.CalledProcessError):
        return f"{exception}\n{exception.stderr or ''}".strip()
    return str(exception)


# --- Entry point ---


def _github(repository: Repository, factory: GithubFactory | None) -> GitHubRepo:
    if factory is not None:
        return factory(repository.checkout)
    return GitHubRepo(
        repository.owner_name.owner,
        repository.owner_name.repo,
        gh=GhCli(cwd=repository.checkout),
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dev")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    git: GitService | None = None,
    github_factory: GithubFactory | None = None,
    new_agent: Callable[[], AgentBuilder] = Agent,
    store: FileExecutionStore | None = None,
    loop_hooks: tuple[LoopHook, ...] = LOOP_HOOKS,
    repositories: Sequence[RepositoryWorkflow] | None = None,
) -> int:
    """Delivers harness Specs through configured repositories, defaulting to implementation on the harness."""
    args = _parse_args(argv)

    log_dir = (args.log_dir or Path.cwd() / LOG_DIR_NAME).resolve()
    # The workflow is the application: it owns the root logger so its own records land in the same file.
    configure_logging(args.log_level, log_file=log_dir / "dev.log", logger="")

    try:
        github = _github(HARNESS, github_factory)
        if repositories is None:
            repositories = (RepositoryWorkflow(HARNESS, partial(implement_ticket, template=PROMPT.read_text())),)
        deps = DevDeps(
            tracker=TicketsTracker(HARNESS.owner_name.owner, HARNESS.owner_name.repo, gh=github.run),
            store=store or FileExecutionStore(log_dir),
            git=git or GitService(),
            new_agent=new_agent,
            loop_hooks=loop_hooks,
            ticket_repository=HARNESS.owner_name,
            github_factory=github_factory,
        )
        return process_specs(repositories, deps)
    except Exception as exception:  # last-resort boundary: log and fail, never crash bare
        logger.exception("unexpected error: %s", exception)
        return 1


if __name__ == "__main__":
    # Example: replace the default call below to target two existing sub-repositories.
    # workspace = HARNESS.checkout / "workspace"
    # implement = partial(implement_ticket, template=PROMPT.read_text())
    # repositories = (
    #     RepositoryWorkflow(
    #         repository=Repository(
    #             checkout=workspace / "api",
    #             worktrees=workspace / "api.worktrees",
    #             owner_name=RepositoryId("acme", "api"),
    #         ),
    #         run_agents=implement,
    #     ),
    #     RepositoryWorkflow(
    #         repository=Repository(
    #             checkout=workspace / "web",
    #             worktrees=workspace / "web.worktrees",
    #             owner_name=RepositoryId("acme", "web"),
    #         ),
    #         run_agents=implement,  # Replace with this repository's agent workflow.
    #     ),
    # )
    # sys.exit(main(sys.argv[1:], repositories=repositories))
    sys.exit(main(sys.argv[1:]))

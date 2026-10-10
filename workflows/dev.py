"""Autonomous dev loop: one branch and worktree per open Spec, one Ticket per fresh agent run.

Control flow, metadata parsing, and the dev result model are owned here,
not by the loop library (see docs/concepts/str-loop-library-workflow-architecture.md).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Any

from loop import (
    DEFAULT,
    Agent,
    AgentBuilder,
    AgentClient,
    AgentRequest,
    BranchStrategy,
    GitOptions,
    LoopHook,
    LoopHookError,
    configure_logging,
)
from workflows.platforms.agent_response import extract_response
from workflows.platforms.git import Commit, WorkflowGit
from workflows.platforms.process import CommandError
from workflows.platforms.work_tracking import (
    GitHubClient,
    Repository,
    RepositoryConfig,
    RepositoryPool,
    RepositoryPoolError,
    Spec,
    Ticket,
    TicketsTracker,
    WorkIdentifier,
)

__all__ = [
    "PROMPT",
    "DevResult",
    "DevResultError",
    "WorkIdentifier",
    "main",
    "parse_dev_result",
    "prompt_args",
]

logger = logging.getLogger("workflow.dev")

# --- Settings: --log-dir and --log-level are the only CLI overrides ---

LOG_DIR_NAME = ".loop"
LOG_LEVEL = "INFO"
LOOP_HOOKS: tuple[LoopHook, ...] = ()
_HARNESS_PATH = Path(__file__).resolve().parents[1]
# Single repo: this checkout is both the harness and the only target repository.
REPOSITORIES: tuple[RepositoryConfig, ...] = (
    RepositoryConfig(
        path=_HARNESS_PATH,
        owner_repo="antshc/loop",
        is_harness=True,
        worktree_root=_HARNESS_PATH / "workspace" / f"{_HARNESS_PATH.name}.worktrees",
    ),
)
MAX_TICKET_FAILURES = 2
PROMPT = Path(__file__).parent / "prompts" / "dev.md"

# --- Errors the dev workflow raises on purpose ---


class DevError(Exception):
    """Base class for every error the dev workflow raises on purpose."""


class PromptError(DevError):
    """A prompt template placeholder has no matching argument."""


class ExecutionStoreError(DevError):
    """The execution log on disk is unreadable."""


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
    identifier: WorkIdentifier,
    initiative_commits: Sequence[str],
    worktree: Path,
    base_branch: str,
    feature_branch: str,
) -> dict[str, str]:
    """Values for every placeholder in the dev prompt template."""
    return {
        "TICKET_JSON": json.dumps(asdict(ticket), indent=2),
        "INITIATIVE_COMMITS": "\n".join(initiative_commits) or "No task commits for this Initiative exist on the feature branch yet.",
        "TASK_ID": str(identifier),
        "COMMIT_SUBJECT_PREFIX": identifier.to_subject(),
        "WORKTREE_PATH": str(worktree),
        "TARGET_BRANCH": base_branch,
        "FEATURE_BRANCH": feature_branch,
    }


# --- The dev result model: decodes the agent's response envelope ---


@dataclass(frozen=True)
class DevResult:
    """The decoded `result` of a run's response envelope: `commit`/`summary`/`verification`, or `reason`."""

    identifier: str
    status: str
    commit: str | None = None
    summary: str | None = None
    verification: str | None = None
    reason: str | None = None


def _decode_envelope(response: str) -> dict[str, Any]:
    try:
        envelope = json.loads(response)
    except json.JSONDecodeError as exception:
        raise DevResultError(f"response is not valid JSON: {exception}") from exception
    if not isinstance(envelope, dict):
        raise DevResultError(f"response is not a JSON object: {response!r}")
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


def parse_dev_result(response: str) -> DevResult:
    """Decodes a run's response envelope for the `completed` and `failed` shapes `dev` understands."""
    envelope = _decode_envelope(response)
    identifier, result = envelope["identifier"], envelope["result"]
    if envelope["status"] == "failed":
        return _failed_result(identifier, result)
    return _completed_result(identifier, result)


def parse_response(output: str, identifier: WorkIdentifier) -> DevResult:
    """Decodes the last response object in the agent's output and checks it answers task `identifier`."""
    response = extract_response(output)
    if response is None:
        raise DevResultError("no response object in agent output")
    dev_result = parse_dev_result(response)
    if dev_result.identifier != str(identifier):
        raise DevResultError(f"response identifier {dev_result.identifier!r} does not match task {identifier!r}")
    return dev_result


# --- Daily per-Ticket execution log of failed attempts ---

Clock = Callable[[], datetime]


class FileExecutionStore:
    """Daily JSON array of per-Spec execution records at `<directory>/<workflow>-execution-log-<UTC date>.json`."""

    def __init__(self, directory: Path, *, workflow: str = "dev", clock: Clock | None = None) -> None:
        self._directory = Path(directory)
        self._workflow = workflow
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def failed_attempts(self, key: str) -> int:
        record = self._find(self._load(), key)
        return record["count"] if record else 0

    def record_failure(
        self,
        key: str,
        *,
        owner: str,
        repo: str,
        task_id: str,
        title: str,
        items: Sequence[int],
    ) -> None:
        records = self._load()
        record = self._find(records, key)
        updated = {
            "owner": owner,
            "repo": repo,
            "type": "spec",
            "task_id": task_id,
            "title": title,
            "task": key,
            "count": (record["count"] if record else 0) + 1,
            "last_run": self._clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_items": list(items),
        }
        if record is None:
            records.append(updated)
        else:
            records[records.index(record)] = updated
        self._save(records)

    def reset(self, key: str) -> None:
        self._save([record for record in self._load() if record["task"] != key])

    def _path(self) -> Path:
        date = self._clock().strftime("%Y-%m-%d")
        return self._directory / f"{self._workflow}-execution-log-{date}.json"

    def _load(self) -> list[dict]:
        path = self._path()
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exception:
            raise ExecutionStoreError(f"execution log is not valid JSON: {path}") from exception
        if not isinstance(data, list):
            raise ExecutionStoreError(f"execution log must be a JSON array: {path}")
        return data

    def _save(self, records: list[dict]) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, separators=(",", ":")))

    @staticmethod
    def _find(records: list[dict], key: str) -> dict | None:
        return next((record for record in records if record["task"] == key), None)


# --- Dependencies `main` wires once and passes through the orchestration ---

GithubFactory = Callable[[Path], GitHubClient]


@dataclass(frozen=True)
class Prompts:
    dev: str


@dataclass(frozen=True)
class DevDeps:
    repository_pool: RepositoryPool
    tracker: TicketsTracker
    store: FileExecutionStore
    git: WorkflowGit
    new_agent: Callable[[], AgentBuilder]
    loop_hooks: tuple[LoopHook, ...]
    prompts: Prompts


# --- Spec orchestration: prepare, deliver, publish, and announce each open Spec ---


class Outcome(Enum):
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


class DevWorkflow:
    """Delivers every open Spec through the dependencies it is constructed with."""

    def __init__(self, deps: DevDeps) -> None:
        self._tracker = deps.tracker
        self._repository_pool = deps.repository_pool
        self._git = deps.git
        self._store = deps.store
        self._new_agent = deps.new_agent
        self._loop_hooks = deps.loop_hooks
        self._prompts = deps.prompts

    def process_specs(self) -> int:
        """Processes every Spec not waiting for a human; returns the process exit code."""
        failed = False
        for spec in self._tracker.specs():
            if spec.awaiting_human:
                continue
            failed = (self._process_spec(spec) is Outcome.FAILED) or failed
        return 1 if failed else 0

    def _process_spec(self, spec: Spec) -> Outcome:
        """Prepare, deliver, and publish one Spec."""
        if spec.target is None or spec.base_branch is None:
            spec.block(f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
            self._tracker.update_spec(spec)
            return Outcome.SKIPPED

        repository = self._repository_pool.get(spec.target)
        if repository is None:
            spec.block(f"dev: repo:target:{spec.target} is not configured in the RepositoryPool")
            self._tracker.update_spec(spec)
            return Outcome.SKIPPED

        try:
            if not self._prepare(spec, repository):
                return Outcome.SKIPPED
            return self._deliver_on_worktree(spec, repository)
        except (CommandError, LoopHookError, subprocess.CalledProcessError) as exception:
            logger.error("spec #%s failed: %s", spec.number, exception)
            spec.comment(f"dev: {self._describe(exception)}")
            self._tracker.update_spec(spec)
            return Outcome.FAILED

    def _prepare(self, spec: Spec, repository: Repository) -> bool:
        """Publishes earlier runs' commits; True only when there are Tickets to deliver."""
        self._git.fetch(repository.path)
        if not spec.has_work:
            self.publish_pull_request(spec, repository, repository.path)
            return False
        if not self._git.remote_branch_exists(repository.path, spec.base_branch):
            spec.hand_to_human(f"dev: target branch {spec.base_branch!r} does not exist on {spec.target}")
            self._tracker.update_spec(spec)
            return False
        # Publish earlier runs' commits from the checkout before the worktree opens.
        self.publish_pull_request(spec, repository, repository.path)
        return True

    def _deliver_on_worktree(self, spec: Spec, repository: Repository) -> Outcome:
        """Opens the feature-branch worktree, delivers the Tickets in it, and publishes before it closes."""
        options = GitOptions(
            root_path=repository.worktree_root,
            repository_path=repository.path,
            strategy=BranchStrategy(spec.feature_branch, f"origin/{spec.base_branch}"),
            loop_hooks=self._loop_hooks,
        )
        with self._new_agent().with_git(options).open() as worktree:
            client = worktree.agent(DEFAULT)
            delivered = self._deliver_tickets(spec, worktree.path, client)
            pull_request_url = self.publish_pull_request(spec, repository, worktree.path)
            if delivered and pull_request_url is not None:
                spec.announce_delivered(pull_request_url)
                self._tracker.update_spec(spec)
        return Outcome.SUCCESS if delivered else Outcome.FAILED

    def _deliver_tickets(self, spec: Spec, worktree: Path, client: AgentClient) -> bool:
        """Delivers each actionable Ticket in order; stops at the first failure."""
        for ticket in spec.tickets:
            if not self._deliver_ticket(spec, ticket, worktree, client):
                return False
        return True

    def _deliver_ticket(self, spec: Spec, ticket: Ticket, worktree: Path, client: AgentClient) -> bool:
        """Fresh agent runs for `ticket` until one is accepted or its failure cap is reached; True on success."""
        identifier = WorkIdentifier(spec.initiative, ticket.number)
        while True:
            head_before = self._git.head(worktree)
            attempt = self._attempt(spec, ticket, identifier, worktree, client, head_before)
            if isinstance(attempt, DevResult):
                spec.close_ticket(
                    ticket.number, f"Delivered in {attempt.commit}.\n\n{attempt.summary}\n\n{attempt.verification}"
                )
                self._tracker.update_spec(spec)
                self._store.reset(ticket.url)
                return True
            self._git.restore(worktree, head_before.sha)
            if self._record_failure(ticket) >= MAX_TICKET_FAILURES:
                spec.escalate(ticket.number, attempt)
                self._tracker.update_spec(spec)
                return False

    def _initiative_commits(self, worktree: Path, spec: Spec) -> list[str]:
        """This Initiative's `ccode(<initiative-id>|` commits on the feature branch since the base branch, as `<short hash> <subject>`."""
        found = self._git.initiative_commits(worktree, spec.base_branch, f"ccode({spec.initiative}|")
        return [f"{commit.sha[:7]} {commit.subject}" for commit in found]

    def _attempt(
        self,
        spec: Spec,
        ticket: Ticket,
        identifier: WorkIdentifier,
        worktree: Path,
        client: AgentClient,
        head_before: Commit,
    ) -> DevResult | str:
        """Runs the agent once and validates its response and Git; returns the accepted result, or the failure reason."""
        args = prompt_args(
            ticket,
            identifier,
            self._initiative_commits(worktree, spec),
            worktree,
            spec.base_branch,
            spec.feature_branch,
        )
        try:
            prompt = render_prompt(self._prompts.dev, args)
            output = client.run(AgentRequest(prompt)).output
            dev_result = parse_response(output, identifier)
        except subprocess.CalledProcessError:
            return "agent process did not exit successfully"
        except DevError as exception:
            return str(exception)
        if dev_result.status == "failed":
            return dev_result.reason or ""

        violation = self._commit_violation(worktree, head_before, identifier, dev_result)
        return dev_result if violation is None else violation

    def _commit_violation(
        self, worktree: Path, head_before: Commit, identifier: WorkIdentifier, dev_result: DevResult
    ) -> str | None:
        """Why the worktree does not hold exactly the one clean, correctly tagged commit the result claims, or None."""
        head = self._git.head(worktree)
        if head.sha == head_before.sha:
            return "HEAD did not change: the agent made no commit"
        new_commits = self._git.commits_since(worktree, head_before.sha)
        if len(new_commits) != 1:
            return f"expected exactly one commit since {head_before.sha}, found {len(new_commits)}"
        prefix = identifier.to_subject()
        if not head.subject.startswith(prefix):
            return f"HEAD subject {head.subject!r} does not start with {prefix!r}"
        if not self._git.is_clean(worktree):
            return "the worktree has uncommitted changes"
        if dev_result.commit != head.sha:
            return f"result.commit {dev_result.commit!r} does not equal HEAD {head.sha!r}"
        return None

    def _record_failure(self, ticket: Ticket) -> int:
        """Counts one failure for `ticket` and returns its persisted total."""
        owner, repo = self._repository_pool.harness.owner_repo.split("/", 1)
        self._store.record_failure(
            ticket.url,
            owner=owner,
            repo=repo,
            task_id=str(ticket.number),
            title=ticket.title,
            items=[ticket.number],
        )
        return self._store.failed_attempts(ticket.url)

    def publish_pull_request(self, spec: Spec, repository: Repository, pusher: Path) -> str | None:
        """Pushes the feature branch from `pusher` and ensures its draft PR when it is ahead of origin.

        Returns the PR URL, or None when there was nothing to publish.
        """
        if not self._git.push_if_ahead(pusher, spec.feature_branch, spec.base_branch):
            return None
        return repository.pull_requests.publish_draft(spec, spec.feature_branch)

    @staticmethod
    def _describe(exception: Exception) -> str:
        if isinstance(exception, subprocess.CalledProcessError):
            return f"{exception}\n{exception.stderr or ''}".strip()
        return str(exception)


# --- Entry point ---


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dev")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    git: WorkflowGit | None = None,
    github_factory: GithubFactory | None = None,
    repositories: Sequence[RepositoryConfig] = REPOSITORIES,
    new_agent: Callable[[], AgentBuilder] = Agent,
    store: FileExecutionStore | None = None,
    loop_hooks: tuple[LoopHook, ...] = LOOP_HOOKS,
) -> int:
    """Runs the dev Workflow once over every open Spec from the current folder; returns the process exit code."""
    args = _parse_args(argv)

    log_dir = (args.log_dir or Path.cwd() / LOG_DIR_NAME).resolve()
    # The workflow is the application: it owns the root logger so its own records land in the same file.
    configure_logging(args.log_level, log_file=log_dir / "dev.log", logger="")

    github_factory = github_factory or (lambda checkout: GitHubClient.for_repo(checkout)[0])
    try:
        repository_pool = RepositoryPool(repositories, github_factory)
    except RepositoryPoolError as exception:
        logger.error("repository pool misconfigured: %s", exception)
        return 1

    deps = DevDeps(
        repository_pool=repository_pool,
        tracker=TicketsTracker(github_factory(repository_pool.harness.path)),
        store=store or FileExecutionStore(log_dir),
        git=git or WorkflowGit(),
        new_agent=new_agent,
        loop_hooks=loop_hooks,
        prompts=Prompts(dev=PROMPT.read_text()),
    )

    try:
        return DevWorkflow(deps).process_specs()
    except Exception as exception:  # last-resort boundary the ticket requires: log and fail, never crash bare
        logger.exception("unexpected error: %s", exception)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Autonomous dev loop: one branch, worktree, and sandbox per open Spec, one Ticket per fresh agent run.

Control flow, metadata parsing, the branch-name rule, and the dev result model are owned here,
not by the loop library (see docs/concepts/str-loop-library-workflow-architecture.md).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from enum import Enum, auto
from pathlib import Path

from loop import (
    AgentClientFactory,
    AgentOptions,
    Cancelled,
    ExecutionStore,
    FileExecutionStore,
    GitClient,
    GitHubClient,
    Hook,
    InMemorySessionStore,
    LoopError,
    NoSandbox,
    Sandbox,
    SandboxFactory,
    SandboxHooks,
    Spec,
    Ticket,
    WorktreeSandbox,
    configure_logging,
    copilot,
    create_sandbox,
    origin_slug,
    same_slug,
)

# Settings as code; --harness-root, --log-dir, and --log-level are the only CLI overrides.
LOG_DIR_NAME = ".loop"
LOG_LEVEL = "INFO"
HOOKS: tuple[Hook, ...] = ()
MAX_TICKET_FAILURES = 2


def _no_sandbox(workspace: Path, cancel: threading.Event) -> Sandbox:
    return NoSandbox(workspace, cancel=cancel)


SANDBOX_FACTORY = _no_sandbox

PROMPT = Path(__file__).parent / "prompts" / "dev.md"
HITL_LABEL = "hitl"
logger = logging.getLogger("workflow.dev")

GithubFactory = Callable[[Path], GitHubClient]

_INITIATIVE = re.compile(r"^(?P<initiative>[^:]+):\s*(?P<title>.+)$")
_TARGET_PREFIX = "repo:target:"
_BASE_PREFIX = "repo:base:"
_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_VERSION = re.compile(r"(\d+(?:\.\d+)+)")


class DevResultError(Exception):
    """The agent's response is missing, not valid JSON, or missing a required `result` field."""


class Outcome(Enum):
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


@dataclass(frozen=True)
class DevResult:
    """The decoded `result` of a run's response envelope: `commit`/`summary`/`verification`, or `reason`."""

    identifier: str
    status: str
    commit: str | None = None
    summary: str | None = None
    verification: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class DevDeps:
    harness_root: Path
    harness_slug: str
    harness_github: GitHubClient
    github_factory: GithubFactory
    git: GitClient
    agent_factory: AgentClientFactory
    sandbox_factory: SandboxFactory
    store: ExecutionStore
    hooks: Sequence[Hook]
    template: str
    cancel: threading.Event


@dataclass(frozen=True)
class SpecRun:
    spec: Spec
    actionable: tuple[Ticket, ...]
    initiative: str
    bare_title: str
    target: str
    base_branch: str
    feature_branch: str
    checkout: Path
    target_github: GitHubClient


def parse_dev_result(response: str) -> DevResult:
    """Decodes a run's response envelope for the `completed` and `failed` shapes `dev` understands."""
    try:
        envelope = json.loads(response)
    except json.JSONDecodeError as exception:
        raise DevResultError(f"response is not valid JSON: {exception}") from exception
    if not isinstance(envelope, dict):
        raise DevResultError(f"response is not a JSON object: {response!r}")
    identifier, status, result = envelope.get("identifier"), envelope.get("status"), envelope.get("result")
    if not isinstance(identifier, str) or status not in ("completed", "failed") or not isinstance(result, dict):
        raise DevResultError(f"malformed response envelope: {envelope!r}")
    if status == "failed":
        reason = result.get("reason")
        if not isinstance(reason, str):
            raise DevResultError(f"failed response is missing result.reason: {result!r}")
        return DevResult(identifier, status, reason=reason)
    commit, summary, verification = result.get("commit"), result.get("summary"), result.get("verification")
    if not isinstance(commit, str) or not isinstance(summary, str) or not isinstance(verification, str):
        raise DevResultError(f"completed response is missing a required result field: {result!r}")
    return DevResult(identifier, status, commit=commit, summary=summary, verification=verification)


def parse_initiative(title: str) -> tuple[str | None, str]:
    """Initiative from the `<initiative>: <title>` prefix, or (None, title) when absent."""
    match = _INITIATIVE.match(title)
    return (None, title) if match is None else (match["initiative"].strip(), match["title"].strip())


def parse_target_label(labels: Sequence[str]) -> str | None:
    """The single `repo:target:<owner/name>` label's value, or None when missing/malformed/duplicated."""
    values = [label[len(_TARGET_PREFIX) :] for label in labels if label.startswith(_TARGET_PREFIX)]
    if len(values) != 1 or not _SLUG.match(values[0]):
        return None
    return values[0]


def parse_base_label(labels: Sequence[str]) -> str | None:
    """The single `repo:base:<branch>` label's value, or None when missing/malformed/duplicated."""
    values = [label[len(_BASE_PREFIX) :] for label in labels if label.startswith(_BASE_PREFIX)]
    return values[0] if len(values) == 1 and values[0] else None


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "spec"


def feature_branch_name(base_branch: str, title: str) -> str:
    """`<version_with_underscores>_<title slug>` when base_branch carries a version, else `<title slug>`."""
    slug = slugify(title)
    match = _VERSION.search(base_branch)
    return slug if match is None else f"{match[1].replace('.', '_')}_{slug}"


def task_id(initiative: str, ticket_number: int) -> str:
    """The response envelope's `identifier`, and the commit subject's parenthesized tag."""
    return f"{initiative}|{ticket_number}"


def commit_subject_prefix(identifier: str) -> str:
    """The required prefix of a Ticket's delivering commit subject."""
    return f"ccode({identifier}): "


def _prompt_args(
    ticket: Ticket,
    identifier: str,
    initiative_commits: Sequence[str],
    worktree: Path,
    base_branch: str,
    feature_branch: str,
) -> dict[str, str]:
    return {
        "TICKET_JSON": json.dumps(asdict(ticket), indent=2),
        "INITIATIVE_COMMITS": "\n".join(initiative_commits) or "No task commits for this Initiative exist on the feature branch yet.",
        "TASK_ID": identifier,
        "WORKTREE_PATH": str(worktree),
        "TARGET_BRANCH": base_branch,
        "FEATURE_BRANCH": feature_branch,
    }


def _record_ticket_failure(run: SpecRun, ticket: Ticket, deps: DevDeps) -> int:
    """Counts one failure for `ticket` and returns its persisted total."""
    owner, repo = deps.harness_slug.split("/", 1)
    deps.store.record_failure(
        ticket.url,
        owner=owner,
        repo=repo,
        task_id=str(ticket.number),
        title=ticket.title,
        items=[ticket.number],
    )
    return deps.store.failed_attempts(ticket.url)


def _hitl(github: GitHubClient, number: int, message: str) -> None:
    github.add_label(number, HITL_LABEL)
    github.comment(number, message)


def _escalate(run: SpecRun, ticket: Ticket, deps: DevDeps, reason: str) -> None:
    message = f"dev: {reason}"
    _hitl(deps.harness_github, ticket.number, message)
    _hitl(deps.harness_github, run.spec.number, message)


def _ensure_pull_request(github: GitHubClient, head: str, base: str, initiative: str, title: str) -> None:
    # One draft PR per feature branch; reruns reuse it.
    if github.find_pull_request(head) is not None:
        return
    github.create_draft_pull_request(head, base, f"{initiative}: {title}")


def _publish_commits(run: SpecRun, deps: DevDeps, worktree: Path, head_before: str | None) -> bool:
    """Push the delivered commits and ensure the draft PR; False when there are none."""
    if head_before is None or deps.git.head(worktree) == head_before:
        return False
    deps.git.push(worktree, run.feature_branch)
    _ensure_pull_request(run.target_github, run.feature_branch, run.base_branch, run.initiative, run.bare_title)
    return True


def _initiative_commits(git: GitClient, worktree: Path, base_branch: str, initiative: str) -> list[str]:
    """This Initiative's `ccode(<initiative-id>|` commits on the feature branch since the base branch."""
    return git.commits_with_prefix(worktree, f"{base_branch}..HEAD", f"ccode({initiative}|")


def _run_and_validate(
    run: SpecRun,
    ticket: Ticket,
    sandbox: WorktreeSandbox,
    deps: DevDeps,
    identifier: str,
    head_before: str,
    prompt_args: dict[str, str],
) -> str | None:
    """Runs the agent and validates its response and Git; returns the failure reason, or None on success."""
    worktree = sandbox.worktree
    try:
        outcome = sandbox.run(deps.agent_factory, deps.template, prompt_args, AgentOptions(session_key=None)).result
    except Cancelled:
        raise
    except LoopError as exception:
        return str(exception)

    try:
        dev_result = parse_dev_result(outcome.response) if outcome.response else None
    except DevResultError as exception:
        return str(exception)
    if dev_result is None:
        return "no response object in agent output"
    if dev_result.identifier != identifier:
        return f"response identifier {dev_result.identifier!r} does not match task {identifier!r}"
    if dev_result.status == "failed":
        return dev_result.reason
    if not outcome.success:
        return "agent process did not exit successfully"

    head = deps.git.head(worktree)
    if head == head_before:
        return "HEAD did not change: the agent made no commit"
    new_commits = deps.git.commits_between(worktree, head_before, head)
    if len(new_commits) != 1:
        return f"expected exactly one commit since {head_before}, found {len(new_commits)}"
    prefix = commit_subject_prefix(identifier)
    subject = deps.git.head_subject(worktree)
    if not subject.startswith(prefix):
        return f"HEAD subject {subject!r} does not start with {prefix!r}"
    if not deps.git.is_clean(worktree):
        return "the worktree has uncommitted changes"
    if dev_result.commit != head:
        return f"result.commit {dev_result.commit!r} does not equal HEAD {head!r}"

    deps.harness_github.close_with_comment(
        ticket.number,
        f"Delivered in {dev_result.commit}.\n\n{dev_result.summary}\n\n{dev_result.verification}",
    )
    return None


def _deliver_ticket(run: SpecRun, ticket: Ticket, sandbox: WorktreeSandbox, deps: DevDeps) -> bool:
    """Fresh agent runs for `ticket` until one is accepted or its failure cap is reached; True on success."""
    worktree = sandbox.worktree
    identifier = task_id(run.initiative, ticket.number)
    while True:
        head_before = deps.git.head(worktree)
        prompt_args = _prompt_args(
            ticket,
            identifier,
            _initiative_commits(deps.git, worktree, run.base_branch, run.initiative),
            worktree,
            run.base_branch,
            run.feature_branch,
        )
        reason = _run_and_validate(run, ticket, sandbox, deps, identifier, head_before, prompt_args)
        if reason is None:
            deps.store.reset(ticket.url)
            return True
        deps.git.reset_to(worktree, head_before)
        if _record_ticket_failure(run, ticket, deps) >= MAX_TICKET_FAILURES:
            _escalate(run, ticket, deps, reason)
            return False


def _deliver_tickets(run: SpecRun, sandbox: WorktreeSandbox, deps: DevDeps) -> bool:
    """Deliver Ticket for each actionable Ticket in order; stops at the first failure."""
    for ticket in run.actionable:
        if not _deliver_ticket(run, ticket, sandbox, deps):
            return False
    return True


def _prepare_run(spec: Spec, deps: DevDeps) -> SpecRun | None:
    # Tickets live on the harness tracker, even when the Spec targets another repo.
    actionable = tuple(deps.harness_github.get_actionable_issues(spec))
    if not actionable:
        return None

    initiative, bare_title = parse_initiative(spec.title)
    initiative = initiative or str(spec.number)
    target = parse_target_label(spec.labels)
    base_branch = parse_base_label(spec.labels)
    if target is None or base_branch is None:
        _hitl(deps.harness_github, spec.number, f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
        return None

    if same_slug(deps.harness_slug, target):
        checkout = deps.harness_root
    else:
        checkout = deps.harness_root / "workspace" / target.split("/", 1)[1]
        checkout_slug = origin_slug(checkout) if checkout.is_dir() else None
        if not same_slug(checkout_slug, target):
            _hitl(
                deps.harness_github,
                spec.number,
                f"dev: expected checkout at {checkout} with origin {target}; found {checkout_slug or 'no clone'}",
            )
            return None

    target_github = deps.harness_github if checkout == deps.harness_root else deps.github_factory(checkout)
    return SpecRun(
        spec=spec,
        actionable=actionable,
        initiative=initiative,
        bare_title=bare_title,
        target=target,
        base_branch=base_branch,
        feature_branch=feature_branch_name(base_branch, bare_title),
        checkout=checkout,
        target_github=target_github,
    )


def _prepare_sandbox(run: SpecRun, deps: DevDeps) -> WorktreeSandbox | None:
    deps.git.fetch(run.checkout)
    if not deps.git.remote_branch_exists(run.checkout, run.base_branch):
        _hitl(
            deps.harness_github,
            run.spec.number,
            f"dev: target branch {run.base_branch!r} does not exist on {run.target}",
        )
        return None
    return create_sandbox(
        deps.git,
        deps.sandbox_factory,
        checkout=run.checkout,
        harness_root=deps.harness_root,
        base=run.base_branch,
        branch=run.feature_branch,
        hooks=SandboxHooks(worktree_ready=tuple(deps.hooks)),
        cancel=deps.cancel,
    )


def _report_cancelled(spec: Spec, worktree: Path | None) -> None:
    if worktree is not None:
        logger.warning("spec #%s run cancelled; worktree kept at %s", spec.number, worktree)
    else:
        logger.info("spec #%s run cancelled", spec.number)


def _process_spec(spec: Spec, deps: DevDeps) -> Outcome:
    """Prepare, deliver, and publish one Spec."""
    run = _prepare_run(spec, deps)
    if run is None:
        return Outcome.SKIPPED

    try:
        sandbox = _prepare_sandbox(run, deps)
        if sandbox is None:
            return Outcome.SKIPPED
    except Cancelled as exception:
        _report_cancelled(spec, exception.worktree)
        return Outcome.SKIPPED
    except LoopError as exception:
        deps.harness_github.comment(run.spec.number, f"dev: {exception}")
        return Outcome.FAILED

    kept_on_cancel = False
    head_before = deps.git.head(sandbox.worktree)
    try:
        delivered = _deliver_tickets(run, sandbox, deps)
        _publish_commits(run, deps, sandbox.worktree, head_before)
        return Outcome.SUCCESS if delivered else Outcome.FAILED
    except Cancelled:
        # Keep the worktree only when it holds uncommitted work.
        kept_on_cancel = deps.git.has_changes(sandbox.worktree)
        _report_cancelled(spec, sandbox.worktree if kept_on_cancel else None)
        return Outcome.SKIPPED
    finally:
        sandbox.close(keep_worktree=kept_on_cancel)


def main(
    argv: list[str] | None = None,
    *,
    git: GitClient | None = None,
    github_factory: GithubFactory | None = None,
    agent_factory: AgentClientFactory | None = None,
    sandbox_factory: SandboxFactory | None = None,
    store: ExecutionStore | None = None,
    hooks: Sequence[Hook] = HOOKS,
    cancel: threading.Event | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="dev")
    parser.add_argument("--harness-root", type=Path, default=None)
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    args = parser.parse_args(argv)

    harness_root = (args.harness_root or Path.cwd()).resolve()
    log_dir = (args.log_dir or harness_root / LOG_DIR_NAME).resolve()
    configure_logging(log_dir / "dev.log", args.log_level)

    harness_slug = origin_slug(harness_root)
    if harness_slug is None:
        logger.error("harness root is not a resolvable github.com git repository: %s", harness_root)
        return 1

    git = git or GitClient()
    github_factory = github_factory or (lambda checkout: GitHubClient.for_repo(checkout)[0])
    harness_github = github_factory(harness_root)
    agent_factory = agent_factory or copilot(InMemorySessionStore())
    sandbox_factory = sandbox_factory or SANDBOX_FACTORY
    store = store or FileExecutionStore(log_dir)
    deps = DevDeps(
        harness_root=harness_root,
        harness_slug=harness_slug,
        harness_github=harness_github,
        github_factory=github_factory,
        git=git,
        agent_factory=agent_factory,
        sandbox_factory=sandbox_factory,
        store=store,
        hooks=hooks,
        template=PROMPT.read_text(),
        cancel=cancel or threading.Event(),
    )

    try:
        failed = False
        for spec in harness_github.get_specs():
            # Specs labeled hitl wait for a human.
            if HITL_LABEL in spec.labels:
                continue
            failed = (_process_spec(spec, deps) is Outcome.FAILED) or failed
        return 1 if failed else 0
    except Exception as exception:  # last-resort boundary the ticket requires: log and fail, never crash bare
        logger.exception("unexpected error: %s", exception)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

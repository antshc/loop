"""Autonomous dev loop: one branch, worktree, and sandbox per open Spec, capped retries.

Control flow, metadata parsing, the branch-name rule, and the agent report parser are
owned here, not by the loop library (see docs/concepts/str-loop-library-workflow-architecture.md).
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
RETRIES = 1
HOOKS: tuple[Hook, ...] = ()


def _no_sandbox(workspace: Path, cancel: threading.Event) -> Sandbox:
    return NoSandbox(workspace, cancel=cancel)


SANDBOX_FACTORY = _no_sandbox

PROMPT = Path(__file__).parent / "prompts" / "dev.md"
HITL_LABEL = "hitl"
# Subject prefix of the agent's commits; the prompt's recent-commits state is filtered by it.
COMMIT_PREFIX = "ccode:"
RECENT_COMMITS = 10
logger = logging.getLogger("workflow.dev")

GithubFactory = Callable[[Path], GitHubClient]

_STATUSES = frozenset({"complete", "partial", "blocked"})
_FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)
_INITIATIVE = re.compile(r"^(?P<initiative>[^:]+):\s*(?P<title>.+)$")
_TARGET_PREFIX = "repo:target:"
_BASE_PREFIX = "repo:base:"
_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_VERSION = re.compile(r"(\d+(?:\.\d+)+)")


class ReportError(Exception):
    """The agent's final fenced JSON block is missing or malformed."""


class Outcome(Enum):
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


@dataclass(frozen=True)
class TicketReport:
    number: int
    status: str
    summary: str


@dataclass(frozen=True)
class AgentReport:
    tickets: tuple[TicketReport, ...]


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
    retries: int
    template: str
    cancel: threading.Event


@dataclass(frozen=True)
class SpecRun:
    spec: Spec
    actionable: tuple[Ticket, ...]
    initiative: str | None
    bare_title: str
    target: str
    base_branch: str
    feature_branch: str
    checkout: Path
    target_github: GitHubClient


@dataclass(frozen=True)
class AgentExecution:
    head_before: str | None
    report: AgentReport | None


def parse_report(text: str) -> AgentReport:
    """The final fenced JSON block of the run's assistant text: `{"tickets": [...]}`."""
    blocks = _FENCE.findall(text)
    if not blocks:
        raise ReportError("no fenced JSON block in agent output")
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError as exception:
        raise ReportError(f"final fenced block is not valid JSON: {exception}") from exception
    if not isinstance(data, dict) or not isinstance(data.get("tickets"), list):
        raise ReportError("report is missing a 'tickets' array")
    tickets = []
    for item in data["tickets"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("number"), int)
            or item.get("status") not in _STATUSES
            or not isinstance(item.get("summary"), str)
        ):
            raise ReportError(f"malformed ticket entry: {item!r}")
        tickets.append(TicketReport(item["number"], item["status"], item["summary"]))
    return AgentReport(tuple(tickets))


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


def _prompt_args(
    spec: Spec,
    actionable: Sequence[Ticket],
    recent_commits: Sequence[str],
    worktree: Path,
    base_branch: str,
    feature_branch: str,
) -> dict[str, str]:
    return {
        "RECENT_COMMITS": "\n".join(recent_commits) or "(none)",
        "SPEC_JSON": json.dumps(asdict(spec), indent=2),
        "TICKETS_JSON": json.dumps([asdict(ticket) for ticket in actionable], indent=2),
        "WORKTREE_PATH": str(worktree),
        "TARGET_BRANCH": base_branch,
        "FEATURE_BRANCH": feature_branch,
    }


def _hitl(github: GitHubClient, spec: Spec, message: str) -> None:
    github.add_label(spec.number, HITL_LABEL)
    github.comment(spec.number, message)


def _ensure_pull_request(github: GitHubClient, head: str, base: str, initiative: str | None, title: str) -> None:
    # One draft PR per feature branch; reruns reuse it.
    if github.find_pull_request(head) is not None:
        return
    github.create_draft_pull_request(head, base, f"{initiative}: {title}" if initiative else title)


def _publish_commits(run: SpecRun, deps: DevDeps, worktree: Path, head_before: str | None) -> bool:
    """Push the agent's new commits and ensure the draft PR; False when there are none."""
    if head_before is None or deps.git.head(worktree) == head_before:
        return False
    deps.git.push(worktree, run.feature_branch)
    _ensure_pull_request(run.target_github, run.feature_branch, run.base_branch, run.initiative, run.bare_title)
    return True


def _record_failure(run: SpecRun, deps: DevDeps, **kwargs: object) -> None:
    owner, repo = deps.harness_slug.split("/", 1)
    deps.store.record_failure(
        run.spec.url,
        owner=owner,
        repo=repo,
        task_id=str(run.spec.number),
        title=run.spec.title,
        items=[ticket.number for ticket in run.actionable],
        **kwargs,
    )


def _fail_attempt(
    run: SpecRun,
    deps: DevDeps,
    exception: Exception,
    *,
    worktree: Path | None = None,
    head_before: str | None = None,
) -> Outcome:
    # Preserve the agent's commits on the feature branch before the failure is recorded.
    if worktree is not None:
        _publish_commits(run, deps, worktree, head_before)
    deps.harness_github.comment(run.spec.number, f"dev: {exception}")
    _record_failure(run, deps)
    return Outcome.FAILED


def _run_report(
    sandbox: WorktreeSandbox,
    agent_factory: AgentClientFactory,
    template: str,
    prompt_args: dict[str, str],
    options: AgentOptions,
    retries: int,
) -> AgentReport | None:
    """Up to 1 + retries fresh Runs on the same WorktreeSandbox; stops at the first valid report."""
    for _ in range(1 + retries):
        result = sandbox.run(agent_factory, template, prompt_args, options).result
        if not result.success:
            continue
        try:
            return parse_report(result.output)
        except ReportError as exception:
            logger.warning("malformed report: %s", exception)
    return None


def _prepare_run(spec: Spec, deps: DevDeps) -> SpecRun | None:
    # Tickets live on the harness tracker, even when the Spec targets another repo.
    actionable = tuple(deps.harness_github.get_actionable_issues(spec))
    if not actionable:
        deps.store.reset(spec.url)
        return None

    initiative, bare_title = parse_initiative(spec.title)
    target = parse_target_label(spec.labels)
    base_branch = parse_base_label(spec.labels)
    if target is None or base_branch is None:
        _hitl(deps.harness_github, spec, f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
        return None

    if same_slug(deps.harness_slug, target):
        checkout = deps.harness_root
    else:
        checkout = deps.harness_root / "workspace" / target.split("/", 1)[1]
        checkout_slug = origin_slug(checkout) if checkout.is_dir() else None
        if not same_slug(checkout_slug, target):
            _hitl(
                deps.harness_github,
                spec,
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
            run.spec,
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


def _execute_agent(
    run: SpecRun,
    sandbox: WorktreeSandbox,
    deps: DevDeps,
    head_before: str | None,
) -> AgentExecution:
    worktree = sandbox.worktree
    prompt_args = _prompt_args(
        run.spec,
        run.actionable,
        deps.git.recent_commits(worktree, COMMIT_PREFIX, RECENT_COMMITS),
        worktree,
        run.base_branch,
        run.feature_branch,
    )
    options = AgentOptions(session_key=None)
    report = _run_report(sandbox, deps.agent_factory, deps.template, prompt_args, options, deps.retries)
    return AgentExecution(head_before, report)


def _apply_report(
    run: SpecRun,
    report: AgentReport,
    deps: DevDeps,
    worktree: Path,
    head_before: str | None,
) -> Outcome:
    # Ignore report entries for Tickets that were not part of this run.
    actionable_numbers = {ticket.number for ticket in run.actionable}
    by_number = {entry.number: entry for entry in report.tickets if entry.number in actionable_numbers}
    any_complete = any(entry.status == "complete" for entry in by_number.values())
    had_changes = _publish_commits(run, deps, worktree, head_before)

    partial_tickets: list[int] = []
    resolved_tickets: list[int] = []
    for ticket in run.actionable:
        entry = by_number.get(ticket.number)
        if entry is None:
            continue
        if entry.status == "complete":
            deps.harness_github.close_with_comment(ticket.number, entry.summary)
            resolved_tickets.append(ticket.number)
        elif entry.status == "partial":
            deps.harness_github.comment(ticket.number, entry.summary)
            partial_tickets.append(ticket.number)
        else:  # blocked
            deps.harness_github.add_label(ticket.number, HITL_LABEL)
            resolved_tickets.append(ticket.number)

    # No commit and no completed Ticket means no progress; it counts as a failed attempt.
    if not had_changes and not any_complete:
        _record_failure(
            run,
            deps,
            partial_tickets=partial_tickets,
            resolved_tickets=resolved_tickets,
        )
        for number in partial_tickets:
            # A Ticket that stays partial across attempts is escalated to a human.
            if deps.store.partial_count(run.spec.url, number) >= 2:
                deps.harness_github.add_label(number, HITL_LABEL)
        return Outcome.FAILED

    deps.store.reset(run.spec.url)
    return Outcome.SUCCESS


def _handle_exhausted_retries(
    run: SpecRun,
    deps: DevDeps,
    worktree: Path,
    head_before: str | None,
) -> Outcome:
    _publish_commits(run, deps, worktree, head_before)
    deps.harness_github.add_label(run.spec.number, HITL_LABEL)
    deps.harness_github.comment(run.spec.number, "dev: every agent run returned no valid report after retries")
    for ticket in run.actionable:
        deps.harness_github.add_label(ticket.number, HITL_LABEL)
    _record_failure(run, deps)
    return Outcome.FAILED


def _complete_run(run: SpecRun, execution: AgentExecution, sandbox: WorktreeSandbox, deps: DevDeps) -> Outcome:
    if execution.report is None:
        return _handle_exhausted_retries(run, deps, sandbox.worktree, execution.head_before)
    return _apply_report(run, execution.report, deps, sandbox.worktree, execution.head_before)


def _report_cancelled(spec: Spec, worktree: Path | None) -> None:
    if worktree is not None:
        logger.warning("spec #%s run cancelled; worktree kept at %s", spec.number, worktree)
    else:
        logger.info("spec #%s run cancelled", spec.number)


def _process_spec(spec: Spec, deps: DevDeps) -> Outcome:
    """Prepare, execute, and complete one Spec."""
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
        return _fail_attempt(run, deps, exception)

    kept_on_cancel = False
    head_before: str | None = None
    try:
        head_before = deps.git.head(sandbox.worktree)
        execution = _execute_agent(run, sandbox, deps, head_before)
        return _complete_run(run, execution, sandbox, deps)
    except Cancelled:
        # Keep the worktree only when it holds uncommitted work.
        kept_on_cancel = deps.git.has_changes(sandbox.worktree)
        _report_cancelled(spec, sandbox.worktree if kept_on_cancel else None)
        return Outcome.SKIPPED
    except LoopError as exception:
        return _fail_attempt(
            run,
            deps,
            exception,
            worktree=sandbox.worktree,
            head_before=head_before,
        )
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
    retries: int = RETRIES,
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
        retries=retries,
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

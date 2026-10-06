"""Autonomous dev loop: one branch, worktree, and sandbox per open Spec, capped retries.

Control flow, metadata parsing, the commit/branch-name rules, and the agent report parser are
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
from dataclasses import dataclass
from pathlib import Path

from loop import (
    AgentClientFactory,
    AgentOptions,
    Cancelled,
    Sandbox,
    SandboxFactory,
    ExecutionStore,
    FileExecutionStore,
    GitClient,
    GitHubClient,
    Hook,
    InMemorySessionStore,
    NoSandbox,
    LoopError,
    WorktreeSandbox,
    SandboxHooks,
    Spec,
    Ticket,
    configure_logging,
    copilot,
    create_sandbox,
    may_attempt,
    origin_slug,
    same_slug,
)
from loop import dry_run as dry_run_agent

# Settings as code; --harness-root, --log-dir, and --log-level are the only CLI overrides.
LOG_DIR_NAME = ".loop"
LOG_LEVEL = "INFO"
RETRIES = 1
DRY_RUN = False
HOOKS: tuple[Hook, ...] = ()


def _no_sandbox(workspace: Path, cancel: threading.Event) -> Sandbox:
    return NoSandbox(workspace, cancel=cancel)


SANDBOX_FACTORY = _no_sandbox

PROMPT = Path(__file__).parent / "prompts" / "dev.md"
HITL_LABEL = "hitl"
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


@dataclass(frozen=True)
class TicketReport:
    number: int
    status: str
    summary: str


@dataclass(frozen=True)
class AgentReport:
    tickets: tuple[TicketReport, ...]


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


def commit_message(initiative: str | None, title: str, tickets: Sequence[TicketReport]) -> tuple[str, str]:
    """Subject carries the Spec title with its Initiative; body lists one line per Ticket summary."""
    subject = f"{initiative}: {title}" if initiative else title
    body = "\n".join(f"#{ticket.number}: {ticket.summary}" for ticket in tickets)
    return subject, body


def _prompt_args(
    spec: Spec,
    actionable: Sequence[Ticket],
    title: str,
    initiative: str | None,
    worktree: Path,
    base_branch: str,
    feature_branch: str,
) -> dict[str, str]:
    return {
        "SPEC_NUMBER": str(spec.number),
        "SPEC_TITLE": title,
        "SPEC_URL": spec.url,
        "INITIATIVE": initiative or "",
        "TICKET_NUMBERS": ", ".join(f"#{ticket.number}" for ticket in actionable),
        "WORKTREE_PATH": str(worktree),
        "TARGET_BRANCH": base_branch,
        "FEATURE_BRANCH": feature_branch,
    }


def _hitl(github: GitHubClient, spec: Spec, message: str) -> None:
    github.add_label(spec.number, HITL_LABEL)
    github.comment(spec.number, message)


def _commit_leftover_changes(
    git: GitClient,
    worktree: Path,
    target_github: GitHubClient,
    feature_branch: str,
    base_branch: str,
    bare_title: str,
    initiative: str | None,
) -> None:
    """Commit and push any uncommitted worktree changes, and ensure the draft PR."""
    if not git.has_changes(worktree):
        return
    # No per-Ticket summaries are available here, so the body is empty.
    subject, body = commit_message(initiative, bare_title, ())
    git.commit(worktree, subject, body)
    git.push(worktree, feature_branch)
    _ensure_pull_request(target_github, feature_branch, base_branch, initiative, bare_title)


def _fail_attempt(
    github: GitHubClient,
    store: ExecutionStore,
    spec: Spec,
    harness_slug: str,
    actionable: Sequence[Ticket],
    exception: Exception,
    *,
    dry_run: bool,
    git: GitClient | None = None,
    worktree: Path | None = None,
    target_github: GitHubClient | None = None,
    feature_branch: str | None = None,
    base_branch: str | None = None,
    bare_title: str | None = None,
    initiative: str | None = None,
) -> None:
    # Preserve partial work on the feature branch before the failure is recorded.
    if not dry_run and git is not None and worktree is not None:
        _commit_leftover_changes(
            git, worktree, target_github, feature_branch, base_branch, bare_title, initiative
        )
    github.comment(spec.number, f"dev: {exception}")
    if dry_run:
        return
    owner, repo = harness_slug.split("/", 1)
    store.record_failure(
        spec.url,
        owner=owner,
        repo=repo,
        task_id=str(spec.number),
        title=spec.title,
        items=[ticket.number for ticket in actionable],
    )


def _ensure_pull_request(github: GitHubClient, head: str, base: str, initiative: str | None, title: str) -> None:
    # One draft PR per feature branch; reruns reuse it.
    if github.find_pull_request(head) is not None:
        return
    github.create_draft_pull_request(head, base, f"{initiative}: {title}" if initiative else title)


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


def _apply_report(
    spec: Spec,
    actionable: Sequence[Ticket],
    report: AgentReport,
    *,
    harness_github: GitHubClient,
    target_github: GitHubClient,
    git: GitClient,
    worktree: Path,
    feature_branch: str,
    base_branch: str,
    bare_title: str,
    initiative: str | None,
    store: ExecutionStore,
    harness_slug: str,
) -> bool:
    # Ignore report entries for Tickets that were not part of this run.
    actionable_numbers = {ticket.number for ticket in actionable}
    by_number = {entry.number: entry for entry in report.tickets if entry.number in actionable_numbers}
    had_changes = git.has_changes(worktree)
    any_complete = any(entry.status == "complete" for entry in by_number.values())

    if had_changes:
        subject, body = commit_message(initiative, bare_title, list(by_number.values()))
        git.commit(worktree, subject, body)
        git.push(worktree, feature_branch)
        _ensure_pull_request(target_github, feature_branch, base_branch, initiative, bare_title)

    partial_tickets: list[int] = []
    resolved_tickets: list[int] = []
    for ticket in actionable:
        entry = by_number.get(ticket.number)
        if entry is None:
            continue
        if entry.status == "complete":
            harness_github.close_with_comment(ticket.number, entry.summary)
            resolved_tickets.append(ticket.number)
        elif entry.status == "partial":
            harness_github.comment(ticket.number, entry.summary)
            partial_tickets.append(ticket.number)
        else:  # blocked
            harness_github.add_label(ticket.number, HITL_LABEL)
            resolved_tickets.append(ticket.number)

    # No commit and no completed Ticket means no progress; it counts toward the attempt cap.
    attempt_failed = not had_changes and not any_complete
    if attempt_failed:
        owner, repo = harness_slug.split("/", 1)
        store.record_failure(
            spec.url,
            owner=owner,
            repo=repo,
            task_id=str(spec.number),
            title=spec.title,
            items=[ticket.number for ticket in actionable],
            partial_tickets=partial_tickets,
            resolved_tickets=resolved_tickets,
        )
        for number in partial_tickets:
            # A Ticket that stays partial across attempts is escalated to a human.
            if store.partial_count(spec.url, number) >= 2:
                harness_github.add_label(number, HITL_LABEL)
    else:
        store.reset(spec.url)
    return not attempt_failed


def _handle_exhausted_retries(
    spec: Spec,
    actionable: Sequence[Ticket],
    *,
    harness_github: GitHubClient,
    target_github: GitHubClient,
    git: GitClient,
    worktree: Path,
    feature_branch: str,
    base_branch: str,
    bare_title: str,
    initiative: str | None,
    store: ExecutionStore,
    harness_slug: str,
) -> None:
    _commit_leftover_changes(git, worktree, target_github, feature_branch, base_branch, bare_title, initiative)
    harness_github.add_label(spec.number, HITL_LABEL)
    harness_github.comment(spec.number, "dev: every agent run returned no valid report after retries")
    for ticket in actionable:
        harness_github.add_label(ticket.number, HITL_LABEL)
    owner, repo = harness_slug.split("/", 1)
    store.record_failure(
        spec.url,
        owner=owner,
        repo=repo,
        task_id=str(spec.number),
        title=spec.title,
        items=[ticket.number for ticket in actionable],
    )


def _report_cancelled(spec: Spec, worktree: Path | None) -> None:
    if worktree is not None:
        logger.warning("spec #%s run cancelled; worktree kept at %s", spec.number, worktree)
    else:
        logger.info("spec #%s run cancelled", spec.number)


def _process_spec(
    spec: Spec,
    *,
    harness_root: Path,
    harness_slug: str,
    harness_github: GitHubClient,
    github_factory: GithubFactory,
    git: GitClient,
    agent_factory: AgentClientFactory,
    sandbox_factory: SandboxFactory,
    store: ExecutionStore,
    hooks: Sequence[Hook],
    retries: int,
    template: str,
    dry_run: bool,
    cancel: threading.Event,
) -> bool | None:
    """Return True/False for an attempted Spec, or None when it was skipped or only explored (dry run)."""
    # Tickets live on the harness tracker, even when the Spec targets another repo.
    actionable = harness_github.get_actionable_issues(spec)
    if not actionable:
        # Nothing left to do, so clear earlier failures; a dry run must not change state.
        if not dry_run:
            store.reset(spec.url)
        return None
    # Skip Specs that already reached the failed-attempt cap.
    if not may_attempt(store, spec.url):
        return None

    initiative, bare_title = parse_initiative(spec.title)
    target = parse_target_label(spec.labels)
    base_branch = parse_base_label(spec.labels)
    if target is None or base_branch is None:
        _hitl(harness_github, spec, f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
        return None

    # The harness is its own checkout; any other target must be cloned under workspace/.
    if same_slug(harness_slug, target):
        checkout = harness_root
    else:
        checkout = harness_root / "workspace" / target.split("/", 1)[1]
        checkout_slug = origin_slug(checkout) if checkout.is_dir() else None
        if not same_slug(checkout_slug, target):
            _hitl(
                harness_github,
                spec,
                f"dev: expected checkout at {checkout} with origin {target}; found {checkout_slug or 'no clone'}",
            )
            return None
    # Pull requests go to the target repo; Ticket updates stay on the harness.
    target_github = harness_github if checkout == harness_root else github_factory(checkout)

    try:
        git.fetch(checkout)
        if not git.remote_branch_exists(checkout, base_branch):
            _hitl(harness_github, spec, f"dev: target branch {base_branch!r} does not exist on {target}")
            return None
        feature_branch = feature_branch_name(base_branch, bare_title)
        sandbox = create_sandbox(
            git,
            sandbox_factory,
            checkout=checkout,
            harness_root=harness_root,
            base=base_branch,
            branch=feature_branch,
            hooks=SandboxHooks(worktree_ready=tuple(hooks)),
            cancel=cancel,
        )
    except Cancelled as exception:
        _report_cancelled(spec, exception.worktree)
        return None
    except LoopError as exception:
        _fail_attempt(harness_github, store, spec, harness_slug, actionable, exception, dry_run=dry_run)
        return False

    worktree = sandbox.worktree
    options = AgentOptions(session_key=None)
    prompt_args = _prompt_args(spec, actionable, bare_title, initiative, worktree, base_branch, feature_branch)
    kept_on_cancel = False
    try:
        if dry_run:
            sandbox.run(agent_factory, template, prompt_args, options)
            return None
        report = _run_report(sandbox, agent_factory, template, prompt_args, options, retries)
        if report is None:
            _handle_exhausted_retries(
                spec,
                actionable,
                harness_github=harness_github,
                target_github=target_github,
                git=git,
                worktree=worktree,
                feature_branch=feature_branch,
                base_branch=base_branch,
                bare_title=bare_title,
                initiative=initiative,
                store=store,
                harness_slug=harness_slug,
            )
            return False
        return _apply_report(
            spec,
            actionable,
            report,
            harness_github=harness_github,
            target_github=target_github,
            git=git,
            worktree=worktree,
            feature_branch=feature_branch,
            base_branch=base_branch,
            bare_title=bare_title,
            initiative=initiative,
            store=store,
            harness_slug=harness_slug,
        )
    except Cancelled:
        # Keep the worktree only when it holds uncommitted work.
        kept_on_cancel = git.has_changes(worktree)
        _report_cancelled(spec, worktree if kept_on_cancel else None)
        return None
    except LoopError as exception:
        _fail_attempt(
            harness_github,
            store,
            spec,
            harness_slug,
            actionable,
            exception,
            dry_run=dry_run,
            git=git,
            worktree=worktree,
            target_github=target_github,
            feature_branch=feature_branch,
            base_branch=base_branch,
            bare_title=bare_title,
            initiative=initiative,
        )
        return False
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
    dry_run: bool = DRY_RUN,
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
    github_factory = github_factory or (lambda checkout: GitHubClient.for_repo(checkout, dry_run=dry_run)[0])
    harness_github = github_factory(harness_root)
    agent_factory = agent_factory or (
        dry_run_agent(InMemorySessionStore()) if dry_run else copilot(InMemorySessionStore())
    )
    sandbox_factory = sandbox_factory or SANDBOX_FACTORY
    store = store or FileExecutionStore(log_dir)
    template = PROMPT.read_text()
    cancel = cancel or threading.Event()

    try:
        failed = False
        for spec in harness_github.get_specs():
            # Specs labeled hitl wait for a human.
            if HITL_LABEL in spec.labels:
                continue
            outcome = _process_spec(
                spec,
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
                template=template,
                dry_run=dry_run,
                cancel=cancel,
            )
            failed = failed or outcome is False
        return 1 if failed else 0
    except Exception as exception:  # last-resort boundary the ticket requires: log and fail, never crash bare
        logger.exception("unexpected error: %s", exception)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

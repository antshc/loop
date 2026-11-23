"""implement_spec's role middlewares: the Stages' request building and checks, one step each."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ..context import AgentCallContext
from ..errors import PromptError, ReplyRejected
from ..integrations import CheckRunner, Commit, WorktreeGit
from ..middleware import Next
from .messages import Proposed, Refined
from .policy import PlanPolicy, chore_subject, initiative_prefix, plan_violation
from .tracker import PullRequest, Tracker

NO_INITIATIVE_COMMITS = (
    "No task commits for this Initiative exist on the feature branch yet."
)

NO_BRANCH_COMMITS = "No commits exist on the feature branch yet."

DIFF_DIR = Path("bin") / "review_diff"

MANIFEST_NAME = "_manifest.tsv"

# Request metadata each placeholder is filled from.
_ROUTE_ARGS = {
    "WORKTREE_PATH": "worktree",
    "TARGET_BRANCH": "base_branch",
    "FEATURE_BRANCH": "feature_branch",
    "INITIATIVE": "initiative",
}

_DIFF_HEADER = re.compile(r"^diff --git a/.+ b/(?P<path>.+)$")


class RouteMiddleware:
    """Pre-agent: the worktree, branches and Initiative from the request's metadata."""

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Fills each route placeholder the metadata carries."""
        for arg, key in _ROUTE_ARGS.items():
            if key in ctx.request.metadata:
                ctx.args[arg] = ctx.request.metadata[key]
        await next(ctx)


class SpecJsonMiddleware:
    """Planner, pre-agent: SPEC_JSON from the tracker's `metadata["spec"]`."""

    def __init__(self, tracker: Tracker) -> None:
        self._tracker = tracker

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Re-reads the Spec, which the tracker owns."""
        spec = await self._tracker.spec(int(ctx.request.metadata["spec"]))
        payload = {
            "number": spec.number,
            "title": spec.title,
            "url": spec.url,
            "body": spec.body,
        }
        ctx.args["SPEC_JSON"] = json.dumps(payload, indent=2)
        await next(ctx)


class TicketJsonMiddleware:
    """Developer, pre-agent: TICKET_JSON from the tracker's `metadata["ticket"]`."""

    def __init__(self, tracker: Tracker) -> None:
        self._tracker = tracker

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Re-reads the Ticket, which the tracker owns."""
        ticket = await self._tracker.ticket(int(ctx.request.metadata["ticket"]))
        payload = {
            "number": ticket.number,
            "title": ticket.title,
            "state": ticket.state,
            "labels": list(ticket.labels),
            "body": ticket.body,
        }
        ctx.args["TICKET_JSON"] = json.dumps(payload, indent=2)
        await next(ctx)


class InitiativeCommitsMiddleware:
    """Developer, pre-agent: the Initiative's Ticket commits already on the branch."""

    def __init__(self, git: WorktreeGit) -> None:
        self._git = git

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Fills INITIATIVE_COMMITS, oldest first."""
        prefix = initiative_prefix(ctx.request.metadata["initiative"])
        commits = await self._git.branch_commits(ctx.request.metadata["worktree"])
        lines = [
            _line(commit) for commit in commits if commit.subject.startswith(prefix)
        ]
        ctx.args["INITIATIVE_COMMITS"] = "\n".join(lines) or NO_INITIATIVE_COMMITS
        await next(ctx)


class BranchCommitsMiddleware:
    """Chore, pre-agent: every commit on the feature branch, for intent."""

    def __init__(self, git: WorktreeGit) -> None:
        self._git = git

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Fills BRANCH_COMMITS, oldest first."""
        commits = await self._git.branch_commits(ctx.request.metadata["worktree"])
        lines = [_line(commit) for commit in commits]
        ctx.args["BRANCH_COMMITS"] = "\n".join(lines) or NO_BRANCH_COMMITS
        await next(ctx)


class PullRequestMiddleware:
    """Chore and reviewer, pre-agent: the feature branch's open pull request."""

    def __init__(self, tracker: Tracker) -> None:
        self._tracker = tracker

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Fills PULL_REQUEST_URL and keeps the pull request in `ctx.items`."""
        branch = ctx.request.metadata["feature_branch"]
        pull_request = await self._tracker.open_pull_request(branch)
        if pull_request is None:
            raise PromptError(f"no open pull request for {branch}")
        ctx.items["pull_request"] = pull_request
        ctx.args["PULL_REQUEST_URL"] = pull_request.url
        await next(ctx)


class DiffCaptureMiddleware:
    """Chore, pre-agent: one diff file per path plus a manifest, removed when the call ends."""

    def __init__(self, tracker: Tracker) -> None:
        self._tracker = tracker

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Fills DIFF_DIR; the captured diff never outlives the call."""
        pull_request: PullRequest = ctx.items["pull_request"]
        diff = await self._tracker.diff(pull_request)
        directory = capture_diff(diff, Path(ctx.request.metadata["worktree"]))
        ctx.args["DIFF_DIR"] = str(directory)
        try:
            await next(ctx)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class PlanPolicyMiddleware:
    """Planner, post-agent: a plan that cannot become Tickets is rejected, not re-asked."""

    def __init__(self, policy: PlanPolicy) -> None:
        self._policy = policy

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Raises `ReplyRejected` naming the first violation."""
        await next(ctx)
        proposed: Proposed = ctx.reply
        violation = plan_violation(proposed.tickets, self._policy.max_tickets)
        if violation is not None:
            raise ReplyRejected(ctx.role, violation)


class ChoreCommitMiddleware:
    """Chore, around each attempt: the agent never moves HEAD; Loop verifies, then commits."""

    def __init__(self, git: WorktreeGit, checks: CheckRunner) -> None:
        self._git = git
        self._checks = checks

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Commits a verified change as `chore(<initiative>): …`; any failure restores the base."""
        path = ctx.request.metadata["worktree"]
        base, status = await self._git.head(path), await self._git.status(path)
        kept = False
        try:
            await next(ctx)
            await self._reject_agent_commit(ctx, path, base)
            if await self._git.status(path) != status:
                await self._verify(ctx)
                await self._git.commit(path, _chore_message(ctx))
            kept = True
        finally:
            if not kept:
                await self._git.restore(path, base)

    async def _reject_agent_commit(
        self, ctx: AgentCallContext, path: str, base: str
    ) -> None:
        head = await self._git.head(path)
        if head != base:
            raise ReplyRejected(
                ctx.role, f"the agent committed ({head}); only Loop commits"
            )

    async def _verify(self, ctx: AgentCallContext) -> None:
        result = await self._checks.run()
        if not result.passed:
            raise ReplyRejected(ctx.role, f"verification failed:\n{result.output}")


def capture_diff(diff: str, worktree: Path) -> Path:
    """Writes one file per changed path and `_manifest.tsv` (`REAL_PATH<TAB>FILE`) under `worktree`."""
    directory = worktree / DIFF_DIR
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    manifest: list[str] = []
    for path, text in _split_by_file(diff):
        name = path.replace("/", "_")
        (directory / name).write_text(text)
        manifest.append(f"{path}\t{name}\n")
    (directory / MANIFEST_NAME).write_text("".join(manifest))
    return directory


def _split_by_file(diff: str) -> list[tuple[str, str]]:
    files: list[tuple[str, list[str]]] = []
    for line in diff.splitlines(keepends=True):
        header = _DIFF_HEADER.match(line.rstrip("\r\n"))
        if header is not None:
            files.append((header["path"], []))
        if files:
            files[-1][1].append(line)
    return [(path, "".join(lines)) for path, lines in files]


def _line(commit: Commit) -> str:
    return f"{commit.sha[:7]} {commit.subject}"


def _chore_message(ctx: AgentCallContext) -> str:
    refined: Refined = ctx.reply
    title = chore_subject(ctx.request.metadata["initiative"], refined.commit_message)
    return f"{title}\n\n{refined.summary}" if refined.summary else title

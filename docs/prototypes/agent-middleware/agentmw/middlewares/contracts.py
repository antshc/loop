"""Repository contracts around one agent attempt, read from `metadata["worktree"]`."""

from __future__ import annotations

from ..context import AgentCallContext
from ..errors import ReplyRejected
from ..integrations import WorktreeGit
from ..middleware import Next


class ReadOnlyMiddleware:
    """HEAD and the git status must equal their values before the attempt."""

    def __init__(self, git: WorktreeGit) -> None:
        self._git = git

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Restores the worktree and raises `ReplyRejected` when the agent changed it."""
        path = ctx.request.metadata["worktree"]
        base, status = await self._git.head(path), await self._git.status(path)
        kept = False
        try:
            await next(ctx)
            violation = await self._violation(path, base, status)
            if violation is not None:
                raise ReplyRejected(ctx.role, violation)
            kept = True
        finally:
            if not kept:
                await self._git.restore(path, base)

    async def _violation(self, path: str, base: str, status: str) -> str | None:
        if await self._git.head(path) != base:
            return "a read-only attempt moved the branch head"
        if await self._git.status(path) != status:
            return "a read-only attempt changed the working tree"
        return None


class AgentCommitsMiddleware:
    """Exactly one agent commit with `metadata["commit_prefix"]`, a clean tree, and `reply.commit` equal to HEAD."""

    def __init__(self, git: WorktreeGit) -> None:
        self._git = git

    async def invoke(self, ctx: AgentCallContext, next: Next) -> None:
        """Fills COMMIT_SUBJECT_PREFIX; any failed attempt returns the branch to its base."""
        path = ctx.request.metadata["worktree"]
        prefix = ctx.request.metadata["commit_prefix"]
        ctx.args["COMMIT_SUBJECT_PREFIX"] = prefix
        base = await self._git.head(path)
        kept = False
        try:
            await next(ctx)
            violation = await self._violation(ctx, path, base, prefix)
            if violation is not None:
                raise ReplyRejected(ctx.role, violation)
            kept = True
        finally:
            if not kept:
                await self._git.restore(path, base)

    async def _violation(
        self, ctx: AgentCallContext, path: str, base: str, prefix: str
    ) -> str | None:
        commits = await self._git.commits_since(path, base)
        if len(commits) != 1:
            return f"expected exactly one new commit, found {len(commits)}"
        if not commits[0].subject.startswith(prefix):
            return f"commit subject {commits[0].subject!r} lacks the prefix {prefix!r}"
        if await self._git.status(path):
            return "the working tree is not clean after the commit"
        reported = getattr(ctx.reply, "commit", None)
        if reported != commits[0].sha:
            return f"reported commit {reported} is not the head {commits[0].sha}"
        return None

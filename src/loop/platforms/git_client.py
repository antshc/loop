from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from loop.errors import Cancelled, CommandError, HookError
from loop.process import CommandResult, checked_output, execute

DEFAULT_HOOK_TIMEOUT_S = 120.0

_REMOTE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$", re.IGNORECASE)
_HEADS = "refs/heads/"
_VERSION = re.compile(r"(\d+(?:\.\d+)+)")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "spec"


@dataclass(frozen=True)
class Branch:
    """A local branch and its `origin` counterpart."""

    name: str

    @classmethod
    def feature(cls, base_branch: str, title: str) -> Branch:
        """`<version_with_underscores>_<title slug>` when base_branch carries a version, else `<title slug>`."""
        slug = slugify(title)
        match = _VERSION.search(base_branch)
        return cls(slug if match is None else f"{match[1].replace('.', '_')}_{slug}")

    @property
    def ref(self) -> str:
        return f"{_HEADS}{self.name}"

    @property
    def remote_ref(self) -> str:
        return f"refs/remotes/origin/{self.name}"

    @property
    def upstream(self) -> str:
        return f"origin/{self.name}"


@dataclass(frozen=True)
class Commit:
    """A commit identified by its (possibly short) hash and its subject line."""

    sha: str
    subject: str = ""

    @classmethod
    def parse(cls, line: str) -> Commit:
        """A Commit from a `%h %s` log line."""
        sha, _, subject = line.partition(" ")
        return cls(sha, subject)

    @staticmethod
    def subject_prefix(identifier: str) -> str:
        """The required prefix of a Ticket's delivering commit subject."""
        return f"ccode({identifier}): "


@dataclass(frozen=True)
class Worktree:
    """A worktree of a Codebase Checkout; `branch` is None on a detached HEAD."""

    path: Path
    branch: Branch | None = None


class GitRunner(Protocol):
    """Runs one git (or Hook) command in a given directory; a non-zero exit is returned, not raised."""

    def __call__(
        self,
        args: Sequence[str] | str,
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
        cancel: threading.Event | None = None,
    ) -> CommandResult: ...


@dataclass(frozen=True)
class Hook:
    """A `worktree-ready` shell command; `timeout_s` overrides the default for this Hook."""

    command: str
    timeout_s: float = DEFAULT_HOOK_TIMEOUT_S


def origin_slug(path: Path, *, run: GitRunner = execute) -> str | None:
    """The `owner/name` of `path`'s `origin` remote on github.com, or None when unresolvable."""
    result = run(("git", "remote", "get-url", "origin"), cwd=path)
    if result.returncode != 0:
        return None
    match = _REMOTE.search(result.stdout.strip())
    return f"{match['owner']}/{match['repo']}" if match else None

class GitClient:
    """The worktree lifecycle an attempt needs: fetch, branch, Hooks, commit, push, remove."""

    def __init__(self, *, run: GitRunner = execute) -> None:
        self._execute = run
        self._lock = threading.Lock()
        self._worktrees: dict[Path, Path] = {}

    def fetch(self, checkout: Path) -> None:
        self._run(("git", "fetch", "--all", "--prune"), cwd=checkout)

    def remote_branch_exists(self, checkout: Path, branch: str) -> bool:
        return self._show_ref(checkout, Branch(branch).remote_ref)

    def create_worktree(
        self,
        checkout: Path,
        branch: str,
        base: str,
        harness_root: Path,
        *,
        on_ready: Sequence[Hook] = (),
        cancel: threading.Event | None = None,
    ) -> Path:
        self._run(("git", "check-ref-format", "--branch", branch), cwd=checkout)
        target = harness_root / "workspace" / f"{checkout.name}.worktrees" / branch
        wanted = Branch(branch)
        with self._lock:
            worktrees = self._list_worktrees(checkout)
            other = next(
                (worktree.path for worktree in worktrees if worktree.branch == wanted and worktree.path != target),
                None,
            )
            if other is not None:
                raise CommandError("git worktree add", None, f"branch {branch!r} is checked out in {other}")
            if any(worktree.path == target for worktree in worktrees):
                if self.has_changes(target):
                    raise CommandError(
                        "git worktree add", None, f"leftover worktree has uncommitted changes: {target}"
                    )
                self._run(("git", "worktree", "remove", "--force", str(target)), cwd=checkout)

            start_ref = wanted.upstream if self.remote_branch_exists(checkout, branch) else Branch(base).upstream
            if self._show_ref(checkout, wanted.ref):
                self._run(("git", "branch", "-f", branch, start_ref), cwd=checkout)
                self._run(("git", "worktree", "add", str(target), branch), cwd=checkout)
            else:
                self._run(("git", "worktree", "add", "-b", branch, str(target), start_ref), cwd=checkout)
            self._worktrees[target] = checkout
            self._exclude_workspace(harness_root)

        try:
            for hook in on_ready:
                self.run_hook(hook, target, cancel)
        except HookError:
            self.remove_worktree(target)
            raise
        except Cancelled:
            if self.has_changes(target):
                raise Cancelled(target)
            self.remove_worktree(target)
            raise
        return target

    def _exclude_workspace(self, harness_root: Path) -> None:
        """Adds `workspace/` to the harness checkout's local exclude list, once."""
        exclude_file = harness_root / ".git" / "info" / "exclude"
        exclude_file.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_file.read_text() if exclude_file.exists() else ""
        if "workspace/" in existing.splitlines():
            return
        with exclude_file.open("a") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write("workspace/\n")

    def has_changes(self, worktree: Path) -> bool:
        return bool(self._run(("git", "status", "--porcelain"), cwd=worktree).strip())

    def head(self, worktree: Path) -> str:
        return self._run(("git", "rev-parse", "HEAD"), cwd=worktree).strip()

    def current_branch(self, path: Path) -> str | None:
        """The checked-out branch of `path`, or None on a detached HEAD."""
        name = self._run(("git", "rev-parse", "--abbrev-ref", "HEAD"), cwd=path).strip()
        return None if name == "HEAD" else name

    def config_get(self, path: Path, key: str) -> str | None:
        result = self._execute(("git", "config", "--get", key), cwd=path)
        return result.stdout.strip() or None if result.returncode == 0 else None

    def commits_between(self, worktree: Path, base: str, tip: str = "HEAD") -> list[str]:
        """Commits reachable from `tip` but not `base`, oldest first."""
        output = self._run(("git", "rev-list", f"{base}..{tip}"), cwd=worktree)
        return list(reversed(output.split()))

    def recent_commits(self, worktree: Path, prefix: str, limit: int) -> list[str]:
        """`<short hash> <subject>` of the newest `limit` commits whose subject starts with `prefix`, newest first."""
        output = self._run(
            ("git", "log", "-n", str(limit), "--fixed-strings", f"--grep={prefix}", "--format=%h %s"), cwd=worktree
        )
        # `--grep` also matches body lines, so keep only subjects that carry the prefix.
        return [line for line in output.splitlines() if Commit.parse(line).subject.startswith(prefix)]

    def head_subject(self, worktree: Path) -> str:
        """The subject line of the commit at HEAD, without its body."""
        return self._run(("git", "log", "-1", "--format=%s"), cwd=worktree).strip()

    def is_clean(self, worktree: Path) -> bool:
        """Whether `worktree` has no staged, unstaged, or untracked changes."""
        return not self.has_changes(worktree)

    def reset_to(self, worktree: Path, commit: str) -> None:
        """Hard-resets `worktree` to `commit` and removes untracked files and directories."""
        self._run(("git", "reset", "--hard", commit), cwd=worktree)
        self._run(("git", "clean", "-fd"), cwd=worktree)

    def commits_with_prefix(self, worktree: Path, range_spec: str, prefix: str) -> list[str]:
        """`<short hash> <subject>` of every commit in `range_spec` whose subject starts with `prefix`, oldest first."""
        output = self._run(
            ("git", "log", "--reverse", "--fixed-strings", f"--grep={prefix}", "--format=%h %s", range_spec),
            cwd=worktree,
        )
        # `--grep` also matches body lines, so keep only subjects that carry the prefix.
        return [line for line in output.splitlines() if Commit.parse(line).subject.startswith(prefix)]

    def branch_ahead_of_remote(self, checkout: Path, branch: str, base: str) -> bool:
        """Whether local `branch` holds commits beyond its `origin` counterpart, or beyond `base` when it has none."""
        local = Branch(branch)
        if not self._show_ref(checkout, local.ref):
            return False
        upstream = local.upstream if self.remote_branch_exists(checkout, branch) else base
        return bool(self._run(("git", "rev-list", f"{upstream}..{branch}"), cwd=checkout).strip())

    def merge(self, checkout: Path, branch: str) -> None:
        with self._lock:
            self._run(("git", "merge", "--no-edit", branch), cwd=checkout)

    def detach(self, worktree: Path) -> None:
        self._run(("git", "checkout", "--detach"), cwd=worktree)

    def delete_branch(self, checkout: Path, branch: str) -> None:
        with self._lock:
            self._run(("git", "branch", "-D", branch), cwd=checkout)

    def commit(self, worktree: Path, subject: str, body: str = "") -> None:
        self._run(("git", "add", "-A"), cwd=worktree)
        args = ("git", "commit", "-m", subject, "-m", body) if body else ("git", "commit", "-m", subject)
        self._run(args, cwd=worktree)

    def push(self, worktree: Path, branch: str) -> None:
        self._run(("git", "push", "origin", branch), cwd=worktree)

    def remove_worktree(self, worktree: Path) -> None:
        try:
            checkout = self._worktrees.pop(worktree)
        except KeyError as exception:
            raise CommandError("git worktree remove", None, f"unknown worktree: {worktree}") from exception
        with self._lock:
            self._run(("git", "worktree", "remove", "--force", str(worktree)), cwd=checkout)

    def _show_ref(self, checkout: Path, ref: str) -> bool:
        return self._execute(("git", "show-ref", "--verify", "--quiet", ref), cwd=checkout).returncode == 0

    def _list_worktrees(self, checkout: Path) -> list[Worktree]:
        output = self._run(("git", "worktree", "list", "--porcelain"), cwd=checkout)
        entries: list[Worktree] = []
        path: Path | None = None
        branch: Branch | None = None
        for line in output.splitlines():
            if line.startswith("worktree "):
                if path is not None:
                    entries.append(Worktree(path, branch))
                path = Path(line[len("worktree ") :])
                branch = None
            elif line.startswith(f"branch {_HEADS}"):
                branch = Branch(line[len(f"branch {_HEADS}") :])
        if path is not None:
            entries.append(Worktree(path, branch))
        return entries

    def run_hook(self, hook: Hook, worktree: Path, cancel: threading.Event | None = None) -> None:
        try:
            result = self._execute(hook.command, cwd=worktree, timeout_s=hook.timeout_s, cancel=cancel)
        except CommandError as exception:
            if cancel is not None and cancel.is_set():
                raise Cancelled() from exception
            raise HookError(hook.command, str(exception)) from exception
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if result.returncode != 0:
            raise HookError(hook.command, result.stdout + result.stderr)

    def _run(self, args: Sequence[str] | str, *, cwd: Path) -> str:
        result = self._execute(args, cwd=cwd)
        return checked_output(args if isinstance(args, str) else " ".join(args), result)

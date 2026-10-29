"""Gates for planner, cleanup, and reviewer work."""

from __future__ import annotations

from pathlib import Path

from workflows.shared.git import GitClient


def made_no_commit(git: GitClient, path: Path, head_sha: str) -> str | None:
    """The violation when the agent moved HEAD: agents edit, the harness commits."""
    head = git.head(path)
    return None if head.sha == head_sha else f"the agent committed ({head.sha}); only the harness commits"


def readonly_violation(git: GitClient, path: Path, head_sha: str, status_before: str) -> str | None:
    """The violation when a read-only agent moved HEAD or changed the working tree compared with `status_before`."""
    violation = made_no_commit(git, path, head_sha)
    if violation is not None:
        return violation
    status = git.status(path)
    return None if status == status_before else "the read-only agent changed the working tree"


def commit_subject(prefix: str, message: str, fallback: str) -> str:
    """`prefix` + the first line of `message` (or `fallback`), without a repeated prefix, capped at 100 characters."""
    lines = message.strip().splitlines()
    line = lines[0].strip() if lines else ""
    line = line.removeprefix(prefix).strip() or fallback.strip()
    return f"{prefix}{line}"[:100].rstrip()

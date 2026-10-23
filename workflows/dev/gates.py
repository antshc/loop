"""Gates for planner, verifier, cleanup, and reviewer work."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from .checkout import GitService


# The machinery that proves the app: a verification fix must never change what grades it.
PROTECTED_PATHS = (
    "proof/",
    "e2e/",
    "playwright.config.ts",
    ".github/skills/",
    ".claude/skills/",
    ".agents/skills/",
)


_SKILL_DIRECTORIES = (".github/skills", ".claude/skills", ".agents/skills")


_DRIVES = re.compile(r"\bdriv\w+", re.IGNORECASE)


_PROVES = re.compile(r"\b(prove|proof)\b", re.IGNORECASE)


def made_no_commit(git: GitService, path: Path, head_sha: str) -> str | None:
    """The violation when the agent moved HEAD: agents edit, the harness commits."""
    head = git.head(path)
    return None if head.sha == head_sha else f"the agent committed ({head.sha}); only the harness commits"


def readonly_violation(git: GitService, path: Path, head_sha: str, status_before: str) -> str | None:
    """The violation when a read-only agent moved HEAD or changed the working tree compared with `status_before`."""
    violation = made_no_commit(git, path, head_sha)
    if violation is not None:
        return violation
    status = git.status(path)
    return None if status == status_before else "the read-only agent changed the working tree"


def protected_changes(files: Iterable[str], protected: Iterable[str] = PROTECTED_PATHS) -> list[str]:
    """The changed files that sit inside the protected paths."""
    prefixes = tuple(protected)
    return [file for file in files if any(file == prefix.rstrip("/") or file.startswith(prefix) for prefix in prefixes)]


def commit_subject(prefix: str, message: str, fallback: str) -> str:
    """`prefix` + the first line of `message` (or `fallback`), without a repeated prefix, capped at 100 characters."""
    lines = message.strip().splitlines()
    line = lines[0].strip() if lines else ""
    line = line.removeprefix(prefix).strip() or fallback.strip()
    return f"{prefix}{line}"[:100].rstrip()


def find_verification_skills(root: Path) -> tuple[Path, ...]:
    """Project skills whose description says they drive the app to prove behavior; skills that disable model invocation are ignored."""
    found: list[Path] = []
    for directory in _SKILL_DIRECTORIES:
        for skill in sorted((root / directory).glob("*/SKILL.md")):
            frontmatter = _frontmatter(skill)
            if frontmatter.get("disable-model-invocation", "").lower() == "true":
                continue
            description = frontmatter.get("description", "")
            if _DRIVES.search(description) and _PROVES.search(description):
                found.append(skill)
    return tuple(found)


def _frontmatter(skill: Path) -> dict[str, str]:
    lines = skill.read_text().splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    values: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip()] = value.strip().strip("\"'")
    return values

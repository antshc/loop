#!/usr/bin/env python3
"""Bring every repository listed in the sibling Harness user settings up to date."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

SETTINGS_FILE_NAME = ".harness.json.user"


@dataclass(frozen=True)
class RepoEntry:
    repository: str  # owner/name
    branch: str
    access: str  # "read" | "write"

    @property
    def name(self) -> str:
        return self.repository.rsplit("/", 1)[-1]


class ConfigError(Exception):
    """A configuration problem that must abort before any repository is touched."""


def load_settings(settings_path: Path) -> dict:
    if not settings_path.is_file():
        return {}
    text = settings_path.read_text(encoding="utf-8")
    return json.loads(text) if text.strip() else {}


def parse_repos(data: dict) -> list[RepoEntry]:
    raw_repos = data.get("repos", [])
    entries: list[RepoEntry] = []
    seen_names: set[str] = set()
    for index, raw in enumerate(raw_repos):
        repository = raw.get("repository")
        branch = raw.get("branch")
        access = raw.get("access", "read")
        if not repository or "/" not in repository:
            raise ConfigError(f"repos[{index}] is missing a valid 'repository' (owner/name)")
        if not branch:
            raise ConfigError(f"repos[{index}] ({repository}) is missing a required 'branch'")
        if access not in ("read", "write"):
            raise ConfigError(f"repos[{index}] ({repository}) has invalid access '{access}' (must be read|write)")
        entry = RepoEntry(repository=repository, branch=branch, access=access)
        if entry.name in seen_names:
            raise ConfigError(f"Duplicate repository name in repos: {entry.name}")
        seen_names.add(entry.name)
        entries.append(entry)
    return entries


def select_entries(entries: list[RepoEntry], names: list[str]) -> list[RepoEntry]:
    if not names:
        return entries
    by_name = {entry.name: entry for entry in entries}
    requested: set[str] = set()
    selected: list[RepoEntry] = []
    for raw_name in names:
        name, _, branch_override = raw_name.partition(":")
        if name in requested:
            raise ConfigError(f"Repository named twice on the command line: {name}")
        requested.add(name)
        entry = by_name.get(name)
        if entry is None:
            raise ConfigError(f"Unknown repository name: {name}")
        selected.append(replace(entry, branch=branch_override) if branch_override else entry)
    return selected


def run_git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def normalize_repository(url: str) -> str:
    value = url.strip().removesuffix(".git")
    if value.startswith("git@"):
        _, _, path = value.partition(":")
        return path
    for prefix in ("https://", "http://", "ssh://"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    parts = value.split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else value


def harness_origin(harness_root: Path) -> str | None:
    result = run_git("remote", "get-url", "origin", cwd=harness_root)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return normalize_repository(result.stdout.strip())


def clone_available() -> bool:
    return shutil.which("gh") is not None


def branch_exists_on_remote(checkout: Path, branch: str) -> bool:
    result = run_git("ls-remote", "--exit-code", "--heads", "origin", branch, cwd=checkout)
    return result.returncode == 0 and bool(result.stdout.strip())


def dirty_tracked_files(checkout: Path) -> list[str]:
    result = run_git("status", "--porcelain", "--untracked-files=no", cwd=checkout)
    return [line[3:] for line in result.stdout.splitlines() if line.strip()]


def local_branch_exists(checkout: Path, branch: str) -> bool:
    return run_git("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=checkout).returncode == 0


def has_diverged(checkout: Path, branch: str) -> bool:
    result = run_git("rev-list", "--left-right", "--count", f"{branch}...origin/{branch}", cwd=checkout)
    if result.returncode != 0:
        return False
    parts = result.stdout.split()
    if len(parts) != 2:
        return False
    ahead, behind = parts
    return int(ahead) > 0 and int(behind) > 0


def clone_repo(entry: RepoEntry, checkout: Path) -> str | None:
    """Return an error message, or None on success."""
    if not clone_available():
        return "no clone tool available (gh not on PATH)"
    checkout.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["gh", "repo", "clone", entry.repository, str(checkout)],
        capture_output=True, text=True, check=False,
    )
    return None if result.returncode == 0 else f"clone failed: {result.stderr.strip()}"


def pull_read(checkout: Path, branch: str) -> tuple[str, str] | None:
    """Return (status, message) on failure, None on success."""
    result = run_git("checkout", "-f", "-B", branch, f"origin/{branch}", cwd=checkout)
    if result.returncode != 0:
        return "failed", f"reset failed: {result.stderr.strip()}"
    return None


def pull_write(checkout: Path, branch: str) -> tuple[str, str] | None:
    """Return (status, message) on failure/skip, None on success."""
    dirty_files = dirty_tracked_files(checkout)
    if dirty_files:
        return "failed", f"dirty tracked files: {', '.join(dirty_files)}"

    if not local_branch_exists(checkout, branch):
        track = run_git("branch", "--track", branch, f"origin/{branch}", cwd=checkout)
        if track.returncode != 0:
            return "failed", f"branch create failed: {track.stderr.strip()}"

    switch = run_git("checkout", branch, cwd=checkout)
    if switch.returncode != 0:
        return "failed", f"switch failed: {switch.stderr.strip()}"

    if has_diverged(checkout, branch):
        return "skipped", f"local branch '{branch}' has diverged from origin"

    pull = run_git("pull", "--ff-only", cwd=checkout)
    if pull.returncode != 0:
        return "failed", f"pull failed: {pull.stderr.strip()}"
    return None


def pull_repo(entry: RepoEntry, harness_root: Path) -> tuple[str, str]:
    checkout = harness_root / "workspace" / entry.name

    cloned = False
    if not checkout.is_dir():
        error = clone_repo(entry, checkout)
        if error is not None:
            return "skipped" if "no clone tool" in error else "failed", error
        cloned = True

    fetch = run_git("fetch", "--all", "--prune", cwd=checkout)
    if fetch.returncode != 0:
        return "failed", f"fetch failed: {fetch.stderr.strip()}"

    if not branch_exists_on_remote(checkout, entry.branch):
        return "skipped", f"branch '{entry.branch}' missing on remote"

    outcome = pull_read(checkout, entry.branch) if entry.access == "read" else pull_write(checkout, entry.branch)
    if outcome is not None:
        return outcome
    return ("cloned" if cloned else "updated"), entry.branch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bring the configured repos checkouts up to date.")
    parser.add_argument("names", nargs="*", help="Optional '<name>[:<branch>]' selection; default is every configured repository.")
    args = parser.parse_args(argv)

    script_dir = Path(__file__).resolve().parent
    harness_root = script_dir.parents[2]
    settings_path = script_dir / SETTINGS_FILE_NAME

    try:
        data = load_settings(settings_path)
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: invalid {settings_path}: {error}", file=sys.stderr)
        return 1

    try:
        entries = parse_repos(data)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if not entries:
        print("no repos configured")
        return 0

    origin = harness_origin(harness_root)
    if origin is not None:
        for entry in entries:
            if entry.repository == origin:
                print(f"error: repos entry '{entry.repository}' is the harness's own origin", file=sys.stderr)
                return 1

    try:
        selected = select_entries(entries, args.names)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    had_problem = False
    for entry in selected:
        status, detail = pull_repo(entry, harness_root)
        print(f"{entry.name}: {status} ({detail})")
        if status in ("skipped", "failed"):
            had_problem = True

    return 1 if had_problem else 0


if __name__ == "__main__":
    raise SystemExit(main())

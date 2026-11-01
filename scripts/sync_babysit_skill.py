"""Synchronize the babysit skill's generated scripts with their canonical sources."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SOURCES = (
    ("scripts/babysit", ""),
    ("software_factory/pull_requests", "software_factory/pull_requests"),
    (
        "software_factory/workflows/pr_babysit",
        "software_factory/workflows/pr_babysit",
    ),
)
PACKAGE_MARKERS = (
    "software_factory/__init__.py",
    "software_factory/github.py",
    "software_factory/workflows/__init__.py",
)
DESTINATION = Path("skills/babysit/scripts")


def _git(root: Path, *args: str) -> bytes:
    """Run a Git query without shell interpretation."""
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=True
    ).stdout


def _files(root: Path, source: str, staged: bool) -> list[Path]:
    """List tracked source paths, plus untracked nonignored files in worktree mode."""
    args = ["ls-files", "-z", "--cached"]
    if not staged:
        args.extend(("--others", "--exclude-standard"))
    args.extend(("--", source))
    return sorted(
        {Path(path.decode("utf-8")) for path in _git(root, *args).split(b"\0") if path}
    )


def _read(root: Path, path: Path, staged: bool) -> bytes | None:
    """Read the committed-to-be blob or an existing worktree file."""
    if staged:
        return _git(root, "show", f":{path.as_posix()}")
    source = root / path
    if not source.is_file() or source.is_symlink():
        return None
    return source.read_bytes()


def expected(root: Path, staged: bool) -> dict[Path, bytes]:
    """Map generated destinations to bytes from their canonical source."""
    result: dict[Path, bytes] = {}
    for source, output in SOURCES:
        base = Path(source)
        for path in _files(root, source, staged):
            content = _read(root, path, staged)
            if content is None:
                continue
            result[DESTINATION / output / path.relative_to(base)] = content
    for source in PACKAGE_MARKERS:
        content = _read(root, Path(source), staged)
        if content is None:
            raise ValueError(f"Missing package marker: {source}")
        result[DESTINATION / source] = content
    if not result:
        raise ValueError("No canonical babysit sources found")
    return result


def synchronize(root: Path, *, staged: bool, check: bool) -> list[str]:
    """Copy only generated files; report drift without modifying them in check mode."""
    want = expected(root, staged)
    scripts = root / DESTINATION
    present = (
        {
            path.relative_to(root)
            for path in scripts.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        if scripts.exists()
        else set()
    )
    changed = sorted(
        {
            path
            for path, data in want.items()
            if path not in present or (root / path).read_bytes() != data
        }
        | (present - set(want))
    )
    if check:
        return [path.as_posix() for path in changed]

    tracked = set(_files(root, DESTINATION.as_posix(), staged=True))
    unknown = (present - set(want)) - tracked
    if unknown:
        raise ValueError(
            f"Untracked files in generated tree: {', '.join(map(str, sorted(unknown)))}"
        )
    for path in present - set(want):
        (root / path).unlink()
    for path, data in want.items():
        target = root / path
        if path not in present or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return [path.as_posix() for path in changed]


def main(argv: list[str] | None = None) -> int:
    """Regenerate the skill, check its drift, or stage the generated changes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staged", action="store_true", help="Read canonical source from the Git index"
    )
    parser.add_argument(
        "--stage-output", action="store_true", help="Stage only generated files"
    )
    parser.add_argument(
        "--check", action="store_true", help="Fail on generated drift without writing"
    )
    args = parser.parse_args(argv)
    if args.check and args.stage_output:
        parser.error("--check and --stage-output cannot be combined")
    root = Path(__file__).resolve().parent.parent
    try:
        changed = synchronize(root, staged=args.staged, check=args.check)
        if args.stage_output:
            _git(root, "add", "-A", "--", DESTINATION.as_posix())
    except (OSError, ValueError, subprocess.CalledProcessError) as exception:
        print(f"Skill synchronization failed: {exception}", file=sys.stderr)
        return 1
    if changed:
        print("Babysit skill drift:\n" + "\n".join(changed))
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    sys.exit(main())

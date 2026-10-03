from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from afk_proto.runtime.context import RunContext
from afk_proto.runtime.discovery import discover_commands
from afk_proto.runtime.errors import ExitCode


DEFAULT_SLICES_DIR = Path(__file__).parents[3] / "slices"


def main(argv: list[str] | None = None) -> int:
    root = argparse.ArgumentParser(add_help=False)
    root.add_argument("--slices-dir", type=Path, default=DEFAULT_SLICES_DIR)
    try:
        known, _ = root.parse_known_args(argv)
    except SystemExit as exception:
        return int(exception.code or ExitCode.USAGE)
    commands = discover_commands(known.slices_dir)

    parser = argparse.ArgumentParser(prog="afk", parents=[root])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--remote-url", default="https://github.com/owner/repo.git")
    parser.add_argument("--log-dir", type=Path, default=Path(".afk-logs"))
    parser.add_argument("--dry-run", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in commands.values():
        command.configure(subparsers.add_parser(command.name, help=command.help))

    try:
        args = parser.parse_args(argv)
    except SystemExit as exception:
        return int(exception.code or ExitCode.USAGE)

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    ctx = RunContext(
        repo=args.repo,
        remote_url=args.remote_url,
        log_dir=args.log_dir,
        dry_run=args.dry_run,
        logger=logging.getLogger("afk"),
    )
    return commands[args.command].run(args, ctx)

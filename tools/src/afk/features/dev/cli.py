#!/usr/bin/env python3
"""Entry point for the AFK dev service.

Parses arguments, validates inputs, and delegates to the dev use case.
Run with --help for usage.

Exit codes:
    0 - success
    2 - usage / argument error (argparse default)
"""

import argparse
import re
from datetime import date
from pathlib import Path

from afk.features.dev.handler import dev
from afk.shared.logging import configure_logging

DEFAULT_MAX_EXECUTIONS = 5


def _repo_dir(value: str) -> Path:
    repo_dir = Path(value).resolve()
    if not repo_dir.is_dir():
        raise argparse.ArgumentTypeError(f"repo-dir does not exist: {repo_dir}")
    return repo_dir


def _github_repo(value: str) -> str:
    if not re.match(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", value):
        raise argparse.ArgumentTypeError(f"Invalid repo format. Expected owner/repo, got: {value}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev.py",
        description="AFK autonomous development service.",
    )
    parser.add_argument("--github_repo_board", type=_github_repo, metavar="owner/repo",
                        help="GitHub repository in owner/repo format.")
    parser.add_argument("--max_executions", nargs="?", type=int, default=DEFAULT_MAX_EXECUTIONS,
                        metavar="max-executions",
                        help=f"Max processing attempts per spec before skipping (default: {DEFAULT_MAX_EXECUTIONS}).")
    parser.add_argument("--agent", default="yolo",
                        help="Agent CLI alias to invoke (default: yolo).")
    parser.add_argument("--prompt", default="/ralph:dev",
                        help="Prompt text passed to the AI agent (default: /ralph:dev).")
    parser.add_argument("--log-dir", type=Path, default=Path("/var/log/ralph"),
                        metavar="log-dir",
                        help="Directory for execution logs (default: /var/log/ralph).")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    log_file = args.log_dir / f"dev-{date.today()}.log"
    configure_logging(log_file)

    dev(args.github_repo_board, args.log_dir, args.max_executions, args.prompt, agent_name=args.agent)

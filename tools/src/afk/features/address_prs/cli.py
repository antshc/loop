#!/usr/bin/env python3
"""Entry point for the AFK PR review-discussion service.

Parses arguments, validates inputs, and delegates to the address use case.
Run with --help for usage.

Exit codes:
    0 - success (may have 0 PRs to process)
    2 - usage / argument error (argparse default)
"""

import argparse
import re
from datetime import date
from pathlib import Path

from afk.features.address_prs.handler import address_prs
from afk.shared.logging import configure_logging

DEFAULT_MAX_EXECUTIONS = 5


def _github_repo(value: str) -> str:
    if not re.match(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", value):
        raise argparse.ArgumentTypeError(f"Invalid repo format. Expected owner/repo, got: {value}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="address_prs.py",
        description="AFK automated PR review-discussion service.",
    )
    parser.add_argument("--github_user", metavar="github-user",
                        help="GitHub username to filter open PRs by author.")
    parser.add_argument("--github_repo", type=_github_repo, metavar="owner/repo",
                        help="GitHub repository in owner/repo format.")
    parser.add_argument("--max_executions", nargs="?", type=int, default=DEFAULT_MAX_EXECUTIONS,
                        metavar="max-executions",
                        help=f"Max processing attempts per PR before skipping (default: {DEFAULT_MAX_EXECUTIONS}).")
    parser.add_argument("--agent", default="yolo",
                        help="Agent CLI alias to invoke (default: yolo).")
    parser.add_argument("--prompt", default="/ralph:address",
                        help="Prompt text passed to the AI agent (default: /ralph:address).")
    parser.add_argument("--log-dir", type=Path, default=Path("/var/log/ralph"),
                        metavar="log-dir",
                        help="Directory for execution logs (default: /var/log/ralph).")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    log_file = args.log_dir / f"address_prs-{date.today()}.log"
    configure_logging(log_file)

    address_prs(args.github_user, args.github_repo, args.log_dir, args.max_executions, args.prompt, agent_name=args.agent)

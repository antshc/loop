"""Reads the open Specs and their Tickets from the harness tracker; delivers nothing."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from loop import configure_logging
from workflows.platforms.work_tracking import GhCli, GitHubRepo, IssueClient, Spec, TicketsTracker

__all__ = ["main", "read_specs"]

logger = logging.getLogger("workflow.dev2")

LOG_DIR_NAME = ".loop"
LOG_LEVEL = "INFO"
_HARNESS_PATH = Path(__file__).resolve().parents[1]
OWNER = "antshc"
REPO = "loop"


def read_specs(tracker: TicketsTracker) -> list[Spec]:
    """Every open Spec with its Tickets, as the tracker reads them."""
    return list(tracker.specs())


def _log_spec(spec: Spec) -> None:
    logger.info(
        "spec #%s %r awaiting_human=%s has_work=%s target=%s base=%s",
        spec.number,
        spec.title,
        spec.awaiting_human,
        spec.has_work,
        spec.target,
        spec.base_branch,
    )
    for ticket in spec.tickets:
        logger.info("  ticket #%s %r state=%s actionable=%s", ticket.number, ticket.title, ticket.state, ticket.actionable)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="dev2")
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--log-level", default=LOG_LEVEL)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, issues: IssueClient | None = None) -> int:
    """Logs every open Spec and its Tickets once; returns the process exit code."""
    args = _parse_args(argv)

    log_dir = (args.log_dir or Path.cwd() / LOG_DIR_NAME).resolve()
    configure_logging(args.log_level, log_file=log_dir / "dev2.log", logger="")

    issues = issues or IssueClient(GitHubRepo(OWNER, REPO, gh=GhCli(cwd=_HARNESS_PATH)))
    tracker = TicketsTracker(issues)

    try:
        for spec in read_specs(tracker):
            _log_spec(spec)
    except Exception as exception:  # last-resort boundary: log and fail, never crash bare
        logger.exception("unexpected error: %s", exception)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

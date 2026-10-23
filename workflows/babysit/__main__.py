from __future__ import annotations

import argparse
import logging
import re
import signal
from pathlib import Path

from .run import run
from ..pr_maintenance.settings import Settings


def main(argv=None) -> int:
    """Run the workflow directly or as one scheduler-owned child process."""
    parser = argparse.ArgumentParser(description="Continuously babysit one user-authored PR")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pr", required=True, help="https://github.com/owner/repo/pull/123")
    args = parser.parse_args(argv)
    match = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)/?", args.pr)
    if not match:
        parser.error("--pr must be a GitHub PR URL")
    logging.basicConfig(level=logging.INFO)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    return run(Settings.load(args.config), match[1], int(match[2]))


if __name__ == "__main__":
    raise SystemExit(main())

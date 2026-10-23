from __future__ import annotations

import argparse
import logging
import re
import signal
from pathlib import Path

from .infrastructure.scheduler import serve
from .infrastructure.state import Blocked
from .settings import Settings
from .workflow.run import run


def main(argv=None) -> int:
    """Start discovery or maintain one explicit PR with the same configuration."""
    parser = argparse.ArgumentParser(description="Discover PRs continuously or babysit one PR")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pr", help="https://github.com/owner/repo/pull/123")
    parser.add_argument("--include-existing", action="store_true")
    parser.add_argument("--retry", help="Retry an enrolled blocked PR during discovery")
    args = parser.parse_args(argv)
    match = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)/?", args.pr or "")
    if args.pr and not match:
        parser.error("--pr must be a GitHub PR URL")
    if args.pr and (args.include_existing or args.retry):
        parser.error("--include-existing and --retry apply to discovery only")
    logging.basicConfig(level=logging.INFO)
    stopping = False

    def request_stop(signum, frame):
        nonlocal stopping
        if args.pr:
            raise KeyboardInterrupt
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        settings = Settings.load(args.config)
        if match:
            return run(settings, match[1], int(match[2]), stop=lambda: stopping)
        return serve(settings, args.config.resolve(), include_existing=args.include_existing,
                     retry=args.retry, stop=lambda: stopping)
    except (Blocked, ValueError, OSError) as exception:
        logging.getLogger(__name__).error("PR maintenance stopped: %s", exception)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

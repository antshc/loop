"""The babysit command: discover PRs continuously, or maintain one explicit PR."""

from __future__ import annotations

import argparse
import logging
import re
import signal
from collections.abc import Sequence
from pathlib import Path

from dishka import Container, Provider, make_container

from software_factory.shared.providers import SharedProvider

from ..discover.scheduler import Scheduler
from ..model import Blocked, DiscoveryRequest, PrTarget, StopSignal
from ..monitor.pr import PrMonitor
from ..settings import Settings
from .providers import BabysitProvider

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 120


def build_container(settings: Settings, *providers: Provider, discovery: DiscoveryRequest | None = None) -> Container:
    """The process container; `providers` override any default registration."""
    return make_container(
        SharedProvider(timeout_s=_GIT_TIMEOUT_S), BabysitProvider(), *providers,
        context={Settings: settings, DiscoveryRequest: discovery or DiscoveryRequest(Path())},
    )


def maintain_pr(container: Container, target: PrTarget) -> int:
    """Maintains one PR in its own REQUEST scope."""
    with container(context={PrTarget: target}) as scope:
        return scope.get(PrMonitor).run()


def main(argv: Sequence[str] | None = None, *, providers: Sequence[Provider] = ()) -> int:
    """Starts discovery or maintains one explicit PR with the same configuration."""
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
    try:
        settings = Settings.load(args.config)
        discovery = DiscoveryRequest(args.config.resolve(), args.include_existing, args.retry)
        container = build_container(settings, *providers, discovery=discovery)
        try:
            stop = container.get(StopSignal)

            def request_stop(signum, frame):
                if args.pr:
                    raise KeyboardInterrupt
                stop.request()

            signal.signal(signal.SIGTERM, request_stop)
            signal.signal(signal.SIGINT, request_stop)
            if match:
                return maintain_pr(container, PrTarget(match[1], int(match[2])))
            return container.get(Scheduler).serve()
        finally:
            container.close()
    except (Blocked, ValueError, OSError) as exception:
        logger.error("PR maintenance stopped: %s", exception)
        return 1

"""Runs the fake workflow and exports its trace to Aspire: `python run_fake.py --help`."""

from __future__ import annotations

import argparse
import sys

from fakeloop import babysitter, scenario
from fakeloop.runner import Run
from fakeloop.scenario import ScenarioOptions, run_scenario
from fakeloop.telemetry import Telemetry

# name -> (session name, default request, runner)
WORKFLOWS = {
    "dev": ("dev", scenario.DEFAULT_REQUEST, run_scenario),
    "pr_babysitter": ("pr_babysitter", babysitter.DEFAULT_REQUEST, babysitter.run_babysitter),
}
PHASES = tuple(dict.fromkeys(scenario.PHASES + babysitter.PHASES))


def main(argv: list[str] | None = None) -> int:
    """Runs one fake session and returns its exit code."""
    args = _parse(argv)
    telemetry = Telemetry()
    adw_name, default_request, play = WORKFLOWS[args.workflow]
    run = Run(telemetry, engineer="fake-engineer", adw_name=adw_name, speed=args.speed)
    print(f"session {run.adw_id}  (trace id in Aspire)")
    options = ScenarioOptions(args.request or default_request, rework=not args.no_rework,
                              fail_at=args.fail_at, fail_ticket=args.fail_ticket)
    try:
        play(run, options)
    except Exception as exception:
        print(f"failed: {exception}", file=sys.stderr)
    finally:
        code = run.finish()
        telemetry.shutdown()
    return code


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", choices=tuple(WORKFLOWS), default="dev",
                        help="dev: plan/develop/review; pr_babysitter: fix PR comments until closed")
    parser.add_argument("--request", help="the engineer's request; defaults per workflow")
    parser.add_argument("--speed", type=float, default=1.0, help="delay multiplier; 0 = instant")
    parser.add_argument("--no-rework", action="store_true",
                        help="skip the failing gate (and the dev review fix loop)")
    parser.add_argument("--fail-at", choices=PHASES, help="raise inside this phase")
    parser.add_argument("--fail-ticket", type=int, choices=(1, 2), default=1,
                        help="dev workflow: the ticket whose --fail-at phase fails")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

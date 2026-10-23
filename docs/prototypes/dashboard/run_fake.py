"""Runs the fake workflow and writes its trace for the dashboard: `python run_fake.py --help`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fakeloop.runner import Run
from fakeloop.scenario import ScenarioOptions, run_scenario
from fakeloop.tracer import Tracer

DATA_DIR = Path(__file__).resolve().parent / "data"
PHASES = ("request", "plan", "develop_1", "review_1", "fix_1", "review_2")


def main(argv: list[str] | None = None) -> int:
    """Runs one fake session and returns its exit code."""
    args = _parse(argv)
    data_dir = Path(args.data_dir)
    tracer = Tracer(data_dir / "sssf.db", data_dir / "events.jsonl")
    run = Run(tracer, data_dir, engineer="fake-engineer", adw_name="fake_plan_develop_review",
              speed=args.speed)
    print(f"session {run.adw_id}  db {data_dir / 'sssf.db'}")
    options = ScenarioOptions(args.request, rework=not args.no_rework, fail_at=args.fail_at)
    try:
        run_scenario(run, options)
    except Exception as exception:
        print(f"failed: {exception}", file=sys.stderr)
        return 1
    return run.finish()


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", default="Add a discount policy to the basket service.")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--speed", type=float, default=1.0, help="delay multiplier; 0 = instant")
    parser.add_argument("--no-rework", action="store_true",
                        help="skip the failing gate and the review fix loop")
    parser.add_argument("--fail-at", choices=PHASES, help="raise inside this phase")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

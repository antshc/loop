"""Runs a workflow with real Copilot agents (or `--dry` fakes) and exports its trace to Aspire: `python run_fake.py --help`."""

from __future__ import annotations

import argparse
import dataclasses
import shutil
import sys

from fakeloop import babysitter, scenario, subagents
from fakeloop.copilot_agent import DEFAULT_MODEL, CopilotAgent, CopilotOptions
from fakeloop.fake_agent import FakeAgent
from fakeloop.model import AgentConfig
from fakeloop.runner import AgentFactory, Run
from fakeloop.scenario import ScenarioOptions, run_scenario
from fakeloop.scratch_repo import ScratchRepo
from fakeloop.telemetry import ROLES, ResourceNames, Telemetry

# name -> (session name, default request, runner)
WORKFLOWS = {
    "dev": ("dev", scenario.DEFAULT_REQUEST, run_scenario),
    "pr_babysitter": ("pr_babysitter", babysitter.DEFAULT_REQUEST, babysitter.run_babysitter),
    "subagents": ("subagents", subagents.DEFAULT_REQUEST, subagents.run_subagents),
}
REAL_WORKFLOWS = ("dev", "subagents")
PHASES = tuple(dict.fromkeys(scenario.PHASES + babysitter.PHASES + subagents.PHASES))


def main(argv: list[str] | None = None) -> int:
    """Runs one session and returns its exit code."""
    args = _parse(argv)
    problem = _problem(args)
    if problem:
        print(problem, file=sys.stderr)
        return 2
    telemetry = Telemetry(ResourceNames(dict(args.resource)))
    adw_name, default_request, play = WORKFLOWS[args.workflow]
    repo = None if args.dry else ScratchRepo(scenario.REAL_FEATURE_BRANCH)
    run = Run(telemetry, engineer="fake-engineer" if args.dry else "engineer", adw_name=adw_name,
              speed=args.speed, agents=_agents(args, repo))
    print(f"session {run.adw_id}  (trace id in Aspire)")
    if repo:
        print(f"agents work in {repo.path}")
    real_dev = repo is not None and args.workflow == "dev"
    request = args.request or (scenario.REAL_REQUEST if real_dev else default_request)
    # Real gates are scripted to pass, so there is nothing to rework.
    options = ScenarioOptions(request, rework=args.dry and not args.no_rework,
                              fail_at=args.fail_at, fail_ticket=args.fail_ticket, repo=repo)
    try:
        play(run, options)
    except Exception as exception:
        print(f"failed: {exception}", file=sys.stderr)
    finally:
        code = run.finish()
        telemetry.shutdown()
        if repo and not args.keep_repo:
            repo.remove()
    return code


def _resource_override(text: str) -> tuple[str, str]:
    role, separator, name = text.partition("=")
    if not separator or role not in ROLES or not name.strip():
        raise argparse.ArgumentTypeError(f"expected ROLE=NAME with ROLE one of {', '.join(ROLES)}")
    return role, name.strip()


def _problem(args: argparse.Namespace) -> str | None:
    """Why this combination of options cannot run, if it cannot."""
    if args.workflow == "subagents" and args.dry:
        return "the subagents workflow has no fake; it only runs real Copilot agents"
    if args.dry:
        return None
    if args.workflow not in REAL_WORKFLOWS:
        return "real Copilot agents are only wired for --workflow dev and subagents; add --dry for the fake"
    if shutil.which("copilot") is None:
        return "copilot is not on PATH; install the Copilot CLI or add --dry"
    return None


def _agents(args: argparse.Namespace, repo: ScratchRepo | None) -> AgentFactory:
    if repo is None:
        return FakeAgent
    options = CopilotOptions(repo=repo.path, model=args.model, timeout=args.agent_timeout)

    def real(config: AgentConfig, system_prompt: str) -> CopilotAgent:
        return CopilotAgent(dataclasses.replace(config, model=args.model), system_prompt, options)

    return real


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry", action="store_true",
                        help="play the scripted fake run instead of spawning real Copilot agents")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="model for every real agent")
    parser.add_argument("--agent-timeout", type=float, default=900.0,
                        help="seconds one real agent may run")
    parser.add_argument("--keep-repo", action="store_true",
                        help="keep the scratch git repo the real agents worked in")
    parser.add_argument("--resource", action="append", default=[], type=_resource_override,
                        metavar="ROLE=NAME",
                        help="export a role as this Aspire resource instead of its own name, "
                             f"e.g. planner=\"Spec planner\"; roles: {', '.join(ROLES)}")
    parser.add_argument("--workflow", choices=tuple(WORKFLOWS), default="dev",
                        help="dev: plan/develop/review; pr_babysitter: fix PR comments until closed; "
                             "subagents (real only): a lead agent that spawns sub-agents")
    parser.add_argument("--request", help="the engineer's request; defaults per workflow and mode")
    parser.add_argument("--speed", type=float, default=1.0, help="delay multiplier; 0 = instant")
    parser.add_argument("--no-rework", action="store_true",
                        help="--dry only: skip the failing gate (and the dev review fix loop)")
    parser.add_argument("--fail-at", choices=PHASES, help="raise inside this phase")
    parser.add_argument("--fail-ticket", type=int, choices=(1, 2), default=1,
                        help="dev workflow: the ticket whose --fail-at phase fails")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())

"""The sub-agent probe workflow: one real lead agent that fans out sub-agents with the `task` tool."""

from __future__ import annotations

from loop import Prompt

from .model import AgentConfig, Attempt, GateCheck, PhaseParams
from .runner import Run
from .scenario import PROMPTS, ScenarioOptions, maybe_fail, read_template

LEAD = AgentConfig("lead", "claude-haiku-4.5", "#34d399")

DEFAULT_REQUEST = "Report what the basket module offers and whether its tests pass."
PHASES = ("request", "investigate")


def run_subagents(run: Run, options: ScenarioOptions) -> None:
    """Plays request, then one `investigate` phase whose lead agent spawns the sub-agents."""
    request = PhaseParams("request", "engineer", "engineer",
                          "Capture the engineer's request that starts the run.")
    with run.phase(request) as ph:
        maybe_fail(options, "request")
        ph.log(input=options.request)
    # Real runs gate on this scripted attempt like the dev workflow's; only the sub-agents are observed.
    attempt = Attempt((), {"status": "success", "summary": "scripted"},
                      (GateCheck("summary_present", True, "scripted"),))
    prompt = Prompt(read_template(PROMPTS / "subagents.md"), {
        "REPOSITORY": "basket", "REQUEST": options.request})
    investigate = PhaseParams("investigate", "agent", "planner",
                              "Fan out read-only sub-agents over the repo and combine their reports.")
    with run.phase(investigate) as ph:
        maybe_fail(options, "investigate")
        run.agent(LEAD, "You coordinate sub-agents; you never read or edit code yourself.").call(
            ph, prompt, "SurveyOutput", "summary_present", [attempt])

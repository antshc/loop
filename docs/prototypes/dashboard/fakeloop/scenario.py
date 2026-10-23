"""The scripted fake workflow: request, plan, develop, commit, review, fix, commit, review."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loop import Prompt

from .fake_agent import Attempt, FakeAgent, ToolStep
from .fake_git import FakeCommit, fake_commit
from .model import AgentConfig, GateCheck, PhaseParams
from .runner import Run

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
# The develop phase wraps the live Dev Workflow prompt unchanged.
IMPLEMENT_TEMPLATE = REPOSITORY_ROOT / "workflows" / "dev" / "prompts" / "implement.md"
PROMPTS = Path(__file__).resolve().parents[1] / "prompts"

PLANNER = AgentConfig("planner", "claude-opus-5", "#a78bfa")
DEVELOPER = AgentConfig("developer", "claude-sonnet-5.5", "#5ad2dd")
REVIEWER = AgentConfig("reviewer", "claude-opus-5", "#e8b64a")

FEATURE_BRANCH = "feature/fake-basket-discount"
TARGET_BRANCH = "main"
WORKTREE = "/tmp/loop.worktrees/fake-basket-discount"


@dataclass(frozen=True)
class ScenarioOptions:
    """What the fake run should do."""

    request: str
    rework: bool = True
    fail_at: str | None = None


def run_scenario(run: Run, options: ScenarioOptions) -> None:
    """Plays the whole fake workflow; an exception marks the run failed."""
    _request(run, options)
    plan = _plan(run, options)
    first = _develop(run, options, plan, "develop_1", [])
    commits = [first]
    review = _review(run, options, plan, "review_1", commits, approve=not options.rework)
    if review["verdict"] == "approved":
        return
    commits.append(_fix(run, options, plan, commits))
    _review(run, options, plan, "review_2", commits, approve=True)


def _request(run: Run, options: ScenarioOptions) -> None:
    params = PhaseParams("request", "engineer", "engineer",
                         "Capture the engineer's request that starts the run.")
    with run.phase(params) as ph:
        _maybe_fail(options, "request")
        ph.log(input=options.request)
        run.sleep(0.5)


def _plan(run: Run, options: ScenarioOptions) -> list[str]:
    steps = (
        ToolStep("view", {"path": "src/Basket.Api/BasketService.cs"}, "142 lines"),
        ToolStep("grep", {"pattern": "ApplyDiscount"}, "3 matches in 2 files"),
        ToolStep("view", {"path": "tests/Basket.UnitTests/BasketServiceTests.cs"}, "88 lines"),
    )
    plan = ["Add DiscountPolicy.", "Apply it in BasketService.", "Cover with unit tests."]
    attempt = Attempt(steps, {"status": "success", "plan": plan},
                      (GateCheck("plan_has_steps", True, f"{len(plan)} steps"),))
    prompt = Prompt(_template(PROMPTS / "plan.md"), {
        "REPOSITORY": "eShop", "TARGET_BRANCH": TARGET_BRANCH, "REQUEST": options.request})
    params = PhaseParams("plan", "agent", "planner",
                         "Turn the request into a plan the developer can implement.")
    with run.phase(params) as ph:
        _maybe_fail(options, "plan")
        FakeAgent(PLANNER, "You plan work; you never edit code.").call(
            ph, prompt, "PlanOutput", "plan_has_steps", [attempt])
    return plan


def _develop(run: Run, options: ScenarioOptions, plan: list[str], name: str,
             commits: list[FakeCommit]) -> FakeCommit:
    commit = FakeCommit("feat(basket): apply discount policy",
                        ("src/Basket.Api/DiscountPolicy.cs", "src/Basket.Api/BasketService.cs",
                         "tests/Basket.UnitTests/DiscountPolicyTests.cs"), 96, 4)
    steps = (
        ToolStep("create", {"path": "src/Basket.Api/DiscountPolicy.cs"}, "created", seconds=0.8),
        ToolStep("edit", {"path": "src/Basket.Api/BasketService.cs"}, "1 hunk applied"),
        ToolStep("create", {"path": "tests/Basket.UnitTests/DiscountPolicyTests.cs"}, "created"),
    )
    failing = Attempt(
        steps + (ToolStep("bash", {"command": "dotnet test --filter DiscountPolicy"},
                          "Failed: 1, Passed: 4", ok=False, seconds=1.2),),
        {"status": "failed", "reason": "1 test failing"},
        (GateCheck("build", True, "0 errors"), GateCheck("tests_pass", False, "1 failing")))
    passing = Attempt(
        (ToolStep("edit", {"path": "src/Basket.Api/DiscountPolicy.cs"}, "rounding fixed"),
         ToolStep("bash", {"command": "dotnet test --filter DiscountPolicy"},
                  "Passed: 5", seconds=1.2)),
        {"status": "success", "commit_subject": commit.subject},
        (GateCheck("build", True, "0 errors"), GateCheck("tests_pass", True, "5 passed")))
    attempts = [failing, passing] if options.rework else [passing]
    params = PhaseParams(name, "agent", "developer",
                         "Implement the plan in the worktree and verify it.",
                         retries=len(attempts) - 1)
    with run.phase(params) as ph:
        _maybe_fail(options, name)
        FakeAgent(DEVELOPER, "You implement tickets in the given worktree.").call(
            ph, _implement_prompt(plan, commits), "BuildOutput", "build_and_tests", attempts)
    fake_commit(run, f"commit_{name}", FEATURE_BRANCH, commit)
    return commit


def _fix(run: Run, options: ScenarioOptions, plan: list[str],
         commits: list[FakeCommit]) -> FakeCommit:
    commit = FakeCommit("fix(basket): guard negative discount",
                        ("src/Basket.Api/DiscountPolicy.cs",
                         "tests/Basket.UnitTests/DiscountPolicyTests.cs"), 14, 2)
    attempt = Attempt(
        (ToolStep("view", {"path": "src/Basket.Api/DiscountPolicy.cs"}, "58 lines"),
         ToolStep("edit", {"path": "src/Basket.Api/DiscountPolicy.cs"}, "1 hunk applied"),
         ToolStep("bash", {"command": "dotnet test --filter DiscountPolicy"},
                  "Passed: 6", seconds=1.2)),
        {"status": "success", "commit_subject": commit.subject},
        (GateCheck("build", True, "0 errors"), GateCheck("tests_pass", True, "6 passed")))
    params = PhaseParams("fix_1", "agent", "developer",
                         "Address the review findings in the worktree.")
    with run.phase(params) as ph:
        _maybe_fail(options, "fix_1")
        FakeAgent(DEVELOPER, "You implement tickets in the given worktree.").call(
            ph, _implement_prompt(plan, commits), "BuildOutput", "build_and_tests", [attempt])
    fake_commit(run, "commit_fix_1", FEATURE_BRANCH, commit)
    return commit


def _review(run: Run, options: ScenarioOptions, plan: list[str], name: str,
            commits: list[FakeCommit], approve: bool) -> dict[str, object]:
    verdict = "approved" if approve else "changes_requested"
    findings = [] if approve else ["DiscountPolicy accepts a negative percentage."]
    attempt = Attempt(
        (ToolStep("bash", {"command": f"git diff {TARGET_BRANCH}...{FEATURE_BRANCH}"},
                  f"{len(commits)} commit(s)"),
         ToolStep("view", {"path": "src/Basket.Api/DiscountPolicy.cs"}, "58 lines")),
        {"status": "success", "verdict": verdict, "findings": findings},
        (GateCheck("verdict_consistent", True, f"{verdict}, {len(findings)} finding(s)"),))
    prompt = Prompt(_template(PROMPTS / "review.md"), {
        "FEATURE_BRANCH": FEATURE_BRANCH, "TARGET_BRANCH": TARGET_BRANCH,
        "PLAN": "\n".join(f"{n}. {step}" for n, step in enumerate(plan, 1)),
        "COMMITS": "\n".join(f"- {commit.subject}" for commit in commits)})
    params = PhaseParams(name, "agent", "reviewer",
                         "Review the branch commits against the plan.")
    with run.phase(params) as ph:
        _maybe_fail(options, name)
        return FakeAgent(REVIEWER, "You review code; you never edit it.").call(
            ph, prompt, "ReviewOutput", "verdict_consistent", [attempt])


def _implement_prompt(plan: list[str], commits: list[FakeCommit]) -> Prompt:
    ticket = {"title": "Apply a basket discount", "body": "\n".join(plan)}
    previous = "\n".join(f"- {commit.subject}" for commit in commits) or "None exist yet."
    return Prompt(_template(IMPLEMENT_TEMPLATE), {
        "WORKTREE_PATH": WORKTREE, "FEATURE_BRANCH": FEATURE_BRANCH,
        "TARGET_BRANCH": TARGET_BRANCH, "TICKET_JSON": json.dumps(ticket, indent=2),
        "INITIATIVE_COMMITS": previous, "COMMIT_SUBJECT_PREFIX": "#1: "})


def _template(path: Path) -> str:
    return path.read_text()


def _maybe_fail(options: ScenarioOptions, name: str) -> None:
    if options.fail_at == name:
        raise RuntimeError(f"fake failure injected at {name}")

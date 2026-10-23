"""The scripted fake workflow: request, plan, develop, commit, review, fix, commit, review."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loop import Prompt

from .fake_git import FakeCommit, fake_commit
from .model import AgentConfig, Attempt, GateCheck, PhaseParams, ToolStep
from .runner import Run
from .scratch_repo import ScratchRepo

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
REAL_FEATURE_BRANCH = "feature/basket-discount"
PHASES = ("request", "plan", "develop_1", "review_1", "fix_1", "review_2")  # develop..review_2 run per ticket
DEFAULT_REQUEST = "Add a discount policy to the basket service."
REAL_REQUEST = "Add a percentage discount to the basket in basket.py, with a test."


@dataclass(frozen=True)
class ScenarioOptions:
    """What the fake run should do."""

    request: str
    rework: bool = True
    fail_at: str | None = None
    fail_ticket: int = 1
    # Set for real runs: the repo the agents work in.
    repo: ScratchRepo | None = None


@dataclass(frozen=True)
class Ticket:
    """One ticket of the spec the dev run delivers."""

    number: int
    title: str
    file: str
    test_file: str
    develop_commit: FakeCommit
    fix_commit: FakeCommit
    finding: str
    # The ticket text the developer sees; the plan itself when empty.
    body: str = ""


TICKETS = (
    Ticket(1, "Add DiscountPolicy", "src/Basket.Api/DiscountPolicy.cs",
           "tests/Basket.UnitTests/DiscountPolicyTests.cs",
           FakeCommit("feat(basket): add discount policy",
                      ("src/Basket.Api/DiscountPolicy.cs",
                       "tests/Basket.UnitTests/DiscountPolicyTests.cs"), 74, 0),
           FakeCommit("fix(basket): guard negative discount",
                      ("src/Basket.Api/DiscountPolicy.cs",
                       "tests/Basket.UnitTests/DiscountPolicyTests.cs"), 14, 2),
           "DiscountPolicy accepts a negative percentage."),
    Ticket(2, "Apply the discount in BasketService", "src/Basket.Api/BasketService.cs",
           "tests/Basket.UnitTests/BasketServiceTests.cs",
           FakeCommit("feat(basket): apply discount policy in the basket",
                      ("src/Basket.Api/BasketService.cs",
                       "tests/Basket.UnitTests/BasketServiceTests.cs"), 38, 4),
           FakeCommit("fix(basket): round discounted total",
                      ("src/Basket.Api/BasketService.cs",), 6, 2),
           "The discounted total is not rounded to two decimals."),
)

# What the real agents are asked to do in the scratch repo; the commits they make replace these.
REAL_TICKETS = (
    Ticket(1, "Add Basket.apply_discount", "basket.py", "test_basket.py",
           FakeCommit("feat: add apply_discount", ("basket.py", "test_basket.py"), 0, 0),
           FakeCommit("fix: guard negative discount", ("basket.py",), 0, 0),
           "apply_discount accepts a negative percentage.",
           "Add `Basket.apply_discount(percent: float)` to basket.py: it stores a percentage "
           "discount (0-100, ValueError outside that range). Cover it in test_basket.py. "
           "Leave `total()` unchanged."),
    Ticket(2, "Show the discounted total", "basket.py", "test_basket.py",
           FakeCommit("feat: total honours the discount", ("basket.py", "test_basket.py"), 0, 0),
           FakeCommit("fix: round discounted total", ("basket.py",), 0, 0),
           "The discounted total is not rounded to two decimals.",
           "Make `Basket.total()` apply the discount stored by `apply_discount`, rounded to two "
           "decimals, and cover it in test_basket.py."),
)


def run_scenario(run: Run, options: ScenarioOptions) -> None:
    """Plays the whole workflow, fake or real; an exception marks the run failed."""
    _request(run, options)
    plan = _plan(run, options)
    for ticket in (REAL_TICKETS if options.repo else TICKETS):
        with run.ticket(ticket.number, ticket.title):
            _deliver(run, options, plan, ticket)


def _deliver(run: Run, options: ScenarioOptions, plan: list[str], ticket: Ticket) -> None:
    # Only the first ticket goes through the failing gate and the review fix loop.
    rework = options.rework and ticket.number == 1
    commits = [_develop(run, options, plan, ticket, rework)]
    review = _review(run, options, plan, ticket, "review_1", commits, approve=not rework)
    if not rework or review.get("verdict") == "approved":
        return
    commits.append(_fix(run, options, plan, ticket, commits))
    _review(run, options, plan, ticket, "review_2", commits, approve=True)


def _request(run: Run, options: ScenarioOptions) -> None:
    params = PhaseParams("request", "engineer", "engineer",
                         "Capture the engineer's request that starts the run.")
    with run.phase(params) as ph:
        maybe_fail(options, "request")
        ph.log(input=options.request)
        run.sleep(0.5)


def _plan(run: Run, options: ScenarioOptions) -> list[str]:
    steps = (
        ToolStep("view", {"path": "src/Basket.Api/BasketService.cs"}, "142 lines"),
        ToolStep("grep", {"pattern": "ApplyDiscount"}, "3 matches in 2 files"),
        ToolStep("view", {"path": "tests/Basket.UnitTests/BasketServiceTests.cs"}, "88 lines"),
    )
    plan = [f"#{ticket.number} {ticket.title}" for ticket in (REAL_TICKETS if options.repo else TICKETS)]
    attempt = Attempt(steps, {"status": "success", "plan": plan},
                      (GateCheck("plan_has_steps", True, f"{len(plan)} steps"),))
    prompt = Prompt(read_template(PROMPTS / "plan.md"), {
        "REPOSITORY": "basket" if options.repo else "eShop", "TARGET_BRANCH": TARGET_BRANCH, "REQUEST": options.request})
    params = PhaseParams("plan", "agent", "planner",
                         "Turn the request into a plan the developer can implement.")
    with run.phase(params) as ph:
        maybe_fail(options, "plan")
        run.agent(PLANNER, "You plan work; you never edit code.").call(
            ph, prompt, "PlanOutput", "plan_has_steps", [attempt])
    return plan


def _develop(run: Run, options: ScenarioOptions, plan: list[str], ticket: Ticket,
             rework: bool) -> FakeCommit:
    commit = ticket.develop_commit
    steps = (
        ToolStep("create" if ticket.number == 1 else "edit", {"path": ticket.file},
                 "created" if ticket.number == 1 else "1 hunk applied", seconds=0.8),
        ToolStep("create", {"path": ticket.test_file}, "created"),
    )
    test = {"command": f"dotnet test --filter {ticket.title.split()[-1]}"}
    failing = Attempt(
        steps + (ToolStep("bash", test, "Failed: 1, Passed: 4", ok=False, seconds=1.2),),
        {"status": "failed", "reason": "1 test failing"},
        (GateCheck("build", True, "0 errors"), GateCheck("tests_pass", False, "1 failing")))
    passing = Attempt(
        (ToolStep("edit", {"path": ticket.file}, "rounding fixed"),
         ToolStep("bash", test, "Passed: 5", seconds=1.2)) if rework
        else steps + (ToolStep("bash", test, "Passed: 5", seconds=1.2),),
        {"status": "success", "commit_subject": commit.subject},
        (GateCheck("build", True, "0 errors"), GateCheck("tests_pass", True, "5 passed")))
    attempts = [failing, passing] if rework else [passing]
    params = PhaseParams("develop_1", "agent", "developer",
                         "Implement the ticket in the worktree and verify it.",
                         retries=len(attempts) - 1)
    with run.phase(params) as ph:
        maybe_fail(options, "develop_1", ticket.number)
        run.agent(DEVELOPER, "You implement tickets in the given worktree.").call(
            ph, _implement_prompt(options, plan, ticket, []), "BuildOutput", "build_and_tests", attempts)
    fake_commit(run, "commit_develop_1", _branch(options), commit)
    return commit


def _fix(run: Run, options: ScenarioOptions, plan: list[str], ticket: Ticket,
         commits: list[FakeCommit]) -> FakeCommit:
    commit = ticket.fix_commit
    attempt = Attempt(
        (ToolStep("view", {"path": ticket.file}, "58 lines"),
         ToolStep("edit", {"path": ticket.file}, "1 hunk applied"),
         ToolStep("bash", {"command": "dotnet test"}, "Passed: 6", seconds=1.2)),
        {"status": "success", "commit_subject": commit.subject},
        (GateCheck("build", True, "0 errors"), GateCheck("tests_pass", True, "6 passed")))
    params = PhaseParams("fix_1", "agent", "developer",
                         "Address the review findings in the worktree.")
    with run.phase(params) as ph:
        maybe_fail(options, "fix_1", ticket.number)
        run.agent(DEVELOPER, "You implement tickets in the given worktree.").call(
            ph, _implement_prompt(options, plan, ticket, commits), "BuildOutput", "build_and_tests",
            [attempt])
    fake_commit(run, "commit_fix_1", _branch(options), commit)
    return commit


def _review(run: Run, options: ScenarioOptions, plan: list[str], ticket: Ticket, name: str,
            commits: list[FakeCommit], approve: bool) -> dict[str, object]:
    verdict = "approved" if approve else "changes_requested"
    findings = [] if approve else [ticket.finding]
    attempt = Attempt(
        (ToolStep("bash", {"command": f"git diff {TARGET_BRANCH}...{_branch(options)}"},
                  f"{len(commits)} commit(s)"),
         ToolStep("view", {"path": ticket.file}, "58 lines")),
        {"status": "success", "verdict": verdict, "findings": findings},
        (GateCheck("verdict_consistent", True, f"{verdict}, {len(findings)} finding(s)"),))
    prompt = Prompt(read_template(PROMPTS / "review.md"), {
        "FEATURE_BRANCH": _branch(options), "TARGET_BRANCH": TARGET_BRANCH,
        "PLAN": "\n".join(f"{n}. {step}" for n, step in enumerate(plan, 1)),
        "COMMITS": "\n".join(f"- {commit.subject}" for commit in commits)})
    params = PhaseParams(name, "agent", "reviewer",
                         f"Review ticket #{ticket.number}'s commits against the plan.")
    with run.phase(params) as ph:
        maybe_fail(options, name, ticket.number)
        return run.agent(REVIEWER, "You review code; you never edit it.").call(
            ph, prompt, "ReviewOutput", "verdict_consistent", [attempt])


def _branch(options: ScenarioOptions) -> str:
    return options.repo.branch if options.repo else FEATURE_BRANCH


def _implement_prompt(options: ScenarioOptions, plan: list[str], ticket: Ticket,
                      commits: list[FakeCommit]) -> Prompt:
    body = {"title": ticket.title, "body": ticket.body or "\n".join(plan)}
    previous = "\n".join(f"- {commit.subject}" for commit in commits) or "None exist yet."
    return Prompt(read_template(IMPLEMENT_TEMPLATE), {
        "WORKTREE_PATH": str(options.repo.path) if options.repo else WORKTREE,
        "FEATURE_BRANCH": _branch(options),
        "TARGET_BRANCH": TARGET_BRANCH, "TICKET_JSON": json.dumps(body, indent=2),
        "INITIATIVE_COMMITS": previous, "COMMIT_SUBJECT_PREFIX": f"#{ticket.number}: "})


def read_template(path: Path) -> str:
    return path.read_text()


def maybe_fail(options: ScenarioOptions, name: str, ticket: int | None = None) -> None:
    if options.fail_at == name and ticket in (None, options.fail_ticket):
        raise RuntimeError(f"fake failure injected at {name}")

"""The fake PR babysitter: fetch the PR, fix its review comments, then poll until it closes."""

from __future__ import annotations

from dataclasses import dataclass

from loop import Prompt

from .fake_agent import Attempt, FakeAgent, ToolStep
from .fake_git import FakeCommit, fake_commit
from .model import AgentConfig, GateCheck, PhaseParams
from .runner import Run
from .scenario import PROMPTS, WORKTREE, ScenarioOptions, maybe_fail, read_template

DEVELOPER = AgentConfig("developer", "claude-sonnet-5.5", "#5ad2dd")

PR_NUMBER = 42
PR_URL = f"https://github.com/eshop/basket/pull/{PR_NUMBER}"
BRANCH = "feature/fake-basket-discount"
DEFAULT_REQUEST = f"Babysit PR #{PR_NUMBER}: fix its review comments and keep watching until it closes."
PHASES = (
    "request",
    "fetch_pr",
    "fix_comments_1",
    "push_1",
    "wait_1",
    "wait_2",
    "fix_comments_2",
    "push_2",
    "wait_3",
    "wait_4",
    "fix_comments_3",
    "push_3",
    "wait_5",
    "wait_6",
)


@dataclass(frozen=True)
class ReviewComment:
    """One unresolved review thread."""

    author: str
    path: str
    line: int
    body: str


# Comments open when the PR is fetched.
INITIAL = (
    ReviewComment(
        "alice", "src/Basket.Api/DiscountPolicy.cs", 31, "Reject negative percentages."
    ),
    ReviewComment(
        "bob",
        "tests/Basket.UnitTests/DiscountPolicyTests.cs",
        12,
        "Add a test for a 100% discount.",
    ),
)
# New comments found by each poll; the last poll finds the PR merged.
POLLS: tuple[tuple[ReviewComment, ...] | None, ...] = (
    (),
    (
        ReviewComment(
            "alice",
            "src/Basket.Api/BasketService.cs",
            77,
            "Round the discounted total to two decimals.",
        ),
        ReviewComment(
            "carol",
            "src/Basket.Api/BasketService.cs",
            80,
            "Log when a discount is applied.",
        ),
    ),
    (),
    (
        ReviewComment(
            "bob", "src/Basket.Api/DiscountPolicy.cs", 18, "Rename `pct` to `percent`."
        ),
    ),
    (),
    None,
)


def run_babysitter(run: Run, options: ScenarioOptions) -> None:
    """Plays the babysitter workflow; an exception marks the run failed."""
    _request(run, options)
    _fetch_pr(run, options)
    _fix_and_push(run, options, INITIAL, 1)
    fixes = 1
    for number, scripted in enumerate(POLLS, 1):
        comments = _poll(run, options, number, scripted)
        if comments is None:
            return
        if comments:
            fixes += 1
            _fix_and_push(run, options, comments, fixes)


def _request(run: Run, options: ScenarioOptions) -> None:
    params = PhaseParams(
        "request",
        "engineer",
        "engineer",
        "Capture the engineer's request that starts the run.",
    )
    with run.phase(params) as ph:
        maybe_fail(options, "request")
        ph.log(input=options.request)
        run.sleep(0.5)


def _fetch_pr(run: Run, options: ScenarioOptions) -> None:
    params = PhaseParams(
        "fetch_pr",
        "code",
        "github",
        "Fetch the pull request, its branch and its unresolved comments.",
    )
    with run.phase(params) as ph:
        maybe_fail(options, "fetch_pr")
        run.sleep(0.8)
        ph.log(
            pr=PR_NUMBER,
            url=PR_URL,
            branch=BRANCH,
            state="open",
            title="feat(basket): apply discount policy",
            unresolved_comments=len(INITIAL),
        )


def _fix_and_push(
    run: Run,
    options: ScenarioOptions,
    comments: tuple[ReviewComment, ...],
    round_number: int,
) -> None:
    name = f"fix_comments_{round_number}"
    commit = FakeCommit(
        f"fix(basket): address review comments (round {round_number})",
        tuple(sorted({comment.path for comment in comments})),
        6 * len(comments),
        2 * len(comments),
    )
    steps = tuple(
        ToolStep("view", {"path": comment.path}, f"line {comment.line}")
        for comment in comments
    )
    edits = tuple(
        ToolStep("edit", {"path": comment.path}, "1 hunk applied")
        for comment in comments
    )
    test = ToolStep("bash", {"command": "dotnet test"}, "Passed: 8", seconds=1.2)
    envelope = {
        "status": "success",
        "addressed": [comment.body for comment in comments],
        "commit_subject": commit.subject,
    }
    gate = (
        GateCheck("comments_addressed", True, f"{len(comments)} of {len(comments)}"),
        GateCheck("tests_pass", True, "8 passed"),
    )
    passing = Attempt(steps + edits + (test,), envelope, gate)
    failing = Attempt(
        steps
        + edits
        + (
            ToolStep(
                "bash",
                {"command": "dotnet test"},
                "Failed: 1, Passed: 7",
                ok=False,
                seconds=1.2,
            ),
        ),
        {"status": "failed", "reason": "1 test failing"},
        (
            GateCheck(
                "comments_addressed", True, f"{len(comments)} of {len(comments)}"
            ),
            GateCheck("tests_pass", False, "1 failing"),
        ),
    )
    attempts = [failing, passing] if options.rework and round_number == 1 else [passing]
    prompt = Prompt(
        read_template(PROMPTS / "fix_comments.md"),
        {
            "PR_URL": PR_URL,
            "WORKTREE_PATH": WORKTREE,
            "BRANCH": BRANCH,
            "COMMENTS": "\n".join(
                f"- @{comment.author} on {comment.path}:{comment.line}: {comment.body}"
                for comment in comments
            ),
        },
    )
    params = PhaseParams(
        name,
        "agent",
        "developer",
        "Address the unresolved review comments in the worktree.",
        retries=len(attempts) - 1,
    )
    with run.phase(params) as ph:
        maybe_fail(options, name)
        FakeAgent(DEVELOPER, "You fix review comments on a pull request branch.").call(
            ph, prompt, "FixOutput", "comments_addressed_and_tests", attempts
        )
    sha = fake_commit(run, f"commit_{name}", BRANCH, commit)
    _push(run, round_number, sha, len(comments))


def _push(run: Run, round_number: int, sha: str, replies: int) -> None:
    params = PhaseParams(
        f"push_{round_number}",
        "code",
        "github",
        "Push the commit and reply on the addressed review threads.",
    )
    with run.phase(params) as ph:
        run.sleep(0.6)
        ph.log(branch=BRANCH, sha=sha[:10], replied_threads=replies)


def _poll(
    run: Run,
    options: ScenarioOptions,
    number: int,
    scripted: tuple[ReviewComment, ...] | None,
) -> tuple[ReviewComment, ...] | None:
    """Polls the PR once; returns its new comments, or None when it is closed."""
    params = PhaseParams(
        f"wait_{number}",
        "code",
        "github",
        "Poll the pull request: stop when it is closed, fix new review comments.",
    )
    with run.phase(params) as ph:
        maybe_fail(options, f"wait_{number}")
        run.sleep(1.5)
        if scripted is None:
            ph.log(poll=number, state="merged", new_comments=0, action="stop")
        else:
            ph.log(
                poll=number,
                state="open",
                new_comments=len(scripted),
                action="fix" if scripted else "wait",
            )
    return scripted

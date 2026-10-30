"""The role prompts implement_spec sends, rendered from fixed requests, equal the goldens captured before the Workflow code moved."""

from __future__ import annotations

from pathlib import Path

import pytest

from software_factory.agents.roles.chore import CHORE, ChoreRequest
from software_factory.agents.roles.developer import DEVELOPER, DeveloperRequest
from software_factory.agents.roles.planner import PLANNER, PlannerRequest
from software_factory.agents.roles.reviewer import REVIEWER, ReviewerRequest
from software_factory.shared.github.issues.graph import SpecGraphSnapshot
from software_factory.shared.github.issues.spec import Spec
from software_factory.stages.specs import feature_branch
from software_factory.stages.tickets import _ticket_payload
from tests.shared.builders import build_issue, build_ticket

GOLDENS = Path(__file__).with_name("goldens") / "prompts"

PROMPTS = ("plan", "implement", "chore", "review")

_WORKTREE = Path("/work/tree")


def render(name: str) -> str:
    """The `name` role's prompt encoded from a fixed request."""
    if name == "implement":
        return DEVELOPER.encode(DeveloperRequest(
            "Checkout|10", _ticket_payload(build_ticket(10, "Add login form")),
            ("ccode(Checkout|9): Add the session store", "ccode(Checkout|8): Add the user model"),
            "ccode(Checkout|10): ", _WORKTREE, "main", "feat/checkout",
        ))
    if name == "chore":
        return CHORE.encode(ChoreRequest(
            "https://github.com/owner/repo/pull/99", _WORKTREE, "main", "feat/checkout", Path("/work/diff"),
            ("abc1234 ccode(Checkout|9): Add the session store", "def5678 ccode(Checkout|10): Add login form"),
        ))
    if name == "plan":
        labels = ("spec", "repo:target:owner/repo", "repo:base:main")
        spec = SpecGraphSnapshot(spec=Spec.of(build_issue(1, "Checkout: Add login page", labels=labels, body="Let customers sign in.")))
        payload = {"number": spec.number, "title": spec.title, "url": spec.url, "body": spec.body, "comments": []}
        return PLANNER.encode(PlannerRequest(Path("/work/checkout"), payload, "main", feature_branch(spec)))
    return REVIEWER.encode(ReviewerRequest("https://github.com/owner/repo/pull/99", Path("/work/checkout"), "Checkout", "head1234"))


@pytest.mark.parametrize("name", PROMPTS)
def test_the_rendered_prompt_equals_the_golden_byte_for_byte(name: str) -> None:
    """Given fixed inputs, when a role prompt is rendered, then it equals the captured golden."""
    assert render(name).encode() == (GOLDENS / f"{name}.md").read_bytes()

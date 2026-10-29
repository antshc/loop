"""Rendered implement_spec prompts and a recorded fake run's tracker writes, captured before the Workflow code moved."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from software_factory.legacy_slices.implement_spec.agents import delegates
from software_factory.legacy_slices.implement_spec.agents.prompts import load_prompt, render_prompt
from software_factory.legacy_slices.implement_spec.composition import dry
from software_factory.legacy_slices.implement_spec.model.records import AgentCommitSubject, ChoreContext, PlanContext, ReviewContext
from software_factory.legacy_slices.implement_spec.composition.entry import main
from software_factory.shared.github.issues.graph import SpecGraphSnapshot
from software_factory.shared.github.issues.spec import Spec
from software_factory.tests.shared.builders import build_issue, build_ticket

GOLDENS = Path(__file__).with_name("goldens")

PROMPTS = ("plan", "implement", "chore", "review")

SCENARIO = GOLDENS / "fake_run_tracker_writes.json"

_WORKTREE = Path("/work/tree")


def prompt_golden(name: str) -> Path:
    return GOLDENS / "prompts" / f"{name}.md"


def render(name: str) -> str:
    """The `name` prompt rendered for fixed inputs."""
    return render_prompt(load_prompt(name), _args(name))


def _args(name: str) -> dict[str, str]:
    if name == "implement":
        ticket = build_ticket(10, "Add login form")
        return delegates.prompt_args(
            ticket,
            AgentCommitSubject("Checkout", 10),
            ("ccode(Checkout|9): Add the session store", "ccode(Checkout|8): Add the user model"),
            _WORKTREE,
            "main",
            "feat/checkout",
        )
    if name == "chore":
        context = ChoreContext(
            "https://github.com/owner/repo/pull/99", "main", "feat/checkout", Path("/work/diff"),
            ("abc1234 ccode(Checkout|9): Add the session store", "def5678 ccode(Checkout|10): Add login form"),
        )
        return delegates.chore_args(context, _WORKTREE)
    if name == "plan":
        labels = ("spec", "repo:target:owner/repo", "repo:base:main")
        spec = SpecGraphSnapshot(spec=Spec.of(build_issue(1, "Checkout: Add login page", labels=labels, body="Let customers sign in.")))
        return delegates.plan_args(PlanContext(spec, "main"), Path("/work/checkout"))
    context = ReviewContext("https://github.com/owner/repo/pull/99", Path("/work/checkout"), "Checkout", "head1234")
    return delegates.review_args(context)


def record_tracker_writes(monkeypatch: pytest.MonkeyPatch, log_dir: Path) -> tuple[int, list[list[str]]]:
    """Runs the Workflow with the agent, Git, GitHub and store faked; returns the exit code and every tracker write in order."""
    writes: list[list[str]] = []

    class RecordingGh(dry.DryGh):
        def __call__(self, args: tuple[str, ...]) -> str:
            if args[0] in ("issue", "pr") and args[:2] not in (("pr", "diff"), ("pr", "list")) or args[:2] == ("api", "--method"):
                writes.append(list(args))
            return super().__call__(args)

    monkeypatch.setattr(dry, "DryGh", RecordingGh)
    argv = ["--log-dir", str(log_dir), "--no-otel", "--fake-speed", "0"]
    for seam in dry.SEAMS:
        argv += ["--fake", seam]
    return main(argv), writes


def render_scenario(writes: list[list[str]]) -> str:
    return json.dumps(writes, indent=2) + "\n"

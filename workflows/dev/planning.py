"""Turns a Spec into a SpecRun: its Tickets, target checkout, and branches."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from loop import Branch, GitHubClient, Spec, Ticket, origin_slug

from .deps import GithubFactory
from .tracker import TicketsTracker


@dataclass(frozen=True)
class SpecRun:
    spec: Spec
    actionable: tuple[Ticket, ...]
    initiative: str
    bare_title: str
    target: str
    base_branch: str
    feature_branch: str
    checkout: Path
    target_github: GitHubClient

def _same_slug(a: str | None, b: str | None) -> bool:
    """Case-insensitive equality for two `origin_slug` results."""
    return a is not None and b is not None and a.casefold() == b.casefold()

def _resolve_checkout(
    spec: Spec,
    target: str,
    actionable: Sequence[Ticket],
    *,
    harness_root: Path,
    harness_slug: str,
    tracker: TicketsTracker,
) -> Path | None:
    """The local checkout of `target`, or None after blocking the Spec when there is none."""
    if _same_slug(harness_slug, target):
        return harness_root
    checkout = harness_root / "workspace" / target.split("/", 1)[1]
    checkout_slug = origin_slug(checkout) if checkout.is_dir() else None
    if not _same_slug(checkout_slug, target):
        tracker.block_spec(
            spec.number,
            actionable,
            f"dev: expected checkout at {checkout} with origin {target}; found {checkout_slug or 'no clone'}",
        )
        return None
    return checkout


def prepare_run(
    spec: Spec,
    *,
    harness_root: Path,
    harness_slug: str,
    tracker: TicketsTracker,
    github_factory: GithubFactory,
) -> SpecRun | None:
    """The SpecRun for `spec`, or None after blocking the Spec when its target cannot be resolved."""
    # Tickets live on the harness tracker, even when the Spec targets another repo.
    tickets = tracker.get_tickets(spec)

    target = spec.target
    base_branch = spec.base_branch
    if target is None or base_branch is None:
        tracker.block_spec(spec.number, tickets, f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
        return None

    checkout = _resolve_checkout(
        spec, target, tickets, harness_root=harness_root, harness_slug=harness_slug, tracker=tracker
    )
    if checkout is None:
        return None

    target_github = tracker.github if checkout == harness_root else github_factory(checkout)
    return SpecRun(
        spec=spec,
        actionable=tickets,
        initiative=spec.initiative,
        bare_title=spec.bare_title,
        target=target,
        base_branch=base_branch,
        feature_branch=Branch.feature(base_branch, spec.bare_title).name,
        checkout=checkout,
        target_github=target_github,
    )

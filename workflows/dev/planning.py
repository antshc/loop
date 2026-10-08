"""Turns a Spec into a SpecRun: its Tickets, target checkout, and branches."""

from __future__ import annotations

from dataclasses import dataclass

from workflows.platforms.work_tracking import Repository, RepositoryPool, Spec, Ticket, TicketsTracker


@dataclass(frozen=True)
class SpecRun:
    spec: Spec
    tickets: tuple[Ticket, ...]
    repository: Repository


def prepare_run(
    spec: Spec,
    *,
    repository_pool: RepositoryPool,
    tracker: TicketsTracker,
) -> SpecRun | None:
    """The SpecRun for `spec`, or None after blocking the Spec when its target cannot be resolved."""
    # Tickets live on the harness tracker, even when the Spec targets another repo.
    tickets = tracker.get_tickets(spec)

    target = spec.target
    base_branch = spec.base_branch
    if target is None or base_branch is None:
        tracker.block_spec(spec.number, tickets, f"dev: cannot resolve repo:target/repo:base labels on {spec.url}")
        return None

    repository = repository_pool.get(target)
    if repository is None:
        tracker.block_spec(spec.number, tickets, f"dev: repo:target:{target} is not configured in the RepositoryPool")
        return None

    return SpecRun(
        spec=spec,
        tickets=tickets,
        repository=repository,
    )

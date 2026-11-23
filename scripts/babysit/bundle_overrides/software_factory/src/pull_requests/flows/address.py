"""The address-discussion fragment: the address agent answers a pull request's pending review discussion in babysit's worktree."""

from __future__ import annotations

from loop import Flow, Route, Verify, stop
from loop.software_factory.stages import workspaces

from software_factory.src.pull_requests.stages import checks
from software_factory.src.pull_requests.stages import workspaces as pr_workspaces
from software_factory.src.pull_requests.stages.address import (
    Commands,
    address_discussion,
)


def address_feedback(
    *,
    commands: Commands,
    verify: Verify,
    on_baseline_red: Route = stop.failed(),
    on_stuck: Route = stop.failed(),
    agent_timeout: float | None = None,
) -> Flow:
    """Clean check, baseline, then the address agent (which commits, pushes, replies and resolves itself), then a clean check again; a red baseline takes `on_baseline_red` when CI is red too, anything stuck takes `on_stuck`; `agent_timeout` bounds the agent run in seconds instead of its role's default."""
    address = address_discussion(commands=commands)
    if agent_timeout is not None:
        address = address.tune(timeout_sec=agent_timeout)
    return Flow("address").within(
        pr_workspaces.pr_worktree(),
        Flow("discussion")
        .stage(workspaces.require_clean().on_hold(on_stuck))
        .stage(
            checks.run_checks(verify=verify)
            .named("baseline")
            .on_fail(on_stuck)
            .on_hold(on_baseline_red)
        )
        .stage(address.on_fail(on_stuck))
        .stage(
            workspaces.require_clean()
            .named("require-clean-after-address")
            .on_hold(on_stuck)
        ),
    )

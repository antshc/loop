"""The address-feedback fragment: answer a pull request's pending review feedback in one placement."""

from __future__ import annotations

from collections.abc import Callable

from software_factory.runtime import Flow, Route, Verify, next_item, stop
from software_factory.stages import checks, pull_requests, workspaces
from software_factory.stages.address import IssueWork, group_feedback, issues, require_no_unclear, resolve_issue


def address_feedback(
    *, message: Callable[[IssueWork], str], verify: Verify, on_baseline_red: Route = stop.failed(), on_stuck: Route = stop.failed(),
    agent_timeout: float | None = None,
) -> Flow:
    """Clean check, baseline, group, resolve each issue (verify, then the runtime commits with `message`), publish once, then hold if anything was unclear; a red baseline takes `on_baseline_red` when CI is red too, anything stuck takes `on_stuck`; `agent_timeout` bounds each agent run in seconds instead of its role's default."""
    group, resolve = group_feedback(), resolve_issue(message=message, verify=verify)
    if agent_timeout is not None:
        group, resolve = group.tune(timeout_sec=agent_timeout), resolve.tune(timeout_sec=agent_timeout)
    return (
        Flow("address")
        .within(workspaces.pr_worktree(), Flow("batch")
            .stage(workspaces.require_clean().on_hold(on_stuck))
            .stage(checks.run_checks(verify=verify).named("baseline").on_fail(on_stuck).on_hold(on_baseline_red))
            .stage(group.on_fail(on_stuck))
            .each(
                issues(),
                Flow("issue").stage(resolve.on_hold(next_item())),
                on_fail=on_stuck,
            )
            .stage(pull_requests.publish_batch().on_fail(on_stuck))
            .stage(require_no_unclear().on_hold(on_stuck)))
    )

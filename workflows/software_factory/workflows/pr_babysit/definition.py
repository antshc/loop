"""pr_babysit as one declarative definition over the Stages library: validate the checkout, then poll one pull request until it closes, doing exactly one thing per poll."""

from __future__ import annotations

from subprocess import CalledProcessError, TimeoutExpired

from loop import Flow, Verify, Workflow, WorktreeError, handoff, reroute, stop

from workflows.software_factory.events.ci import CiFixed
from workflows.software_factory.events.feedback import FeedbackBatch
from workflows.software_factory.events.pull_requests import (
    CiFailing,
    CopilotDue,
    MergeConflict,
    NextAction,
    PullRequestClosed,
    PullRequestWork,
    ReadinessCheck,
    Unsupported,
)
from workflows.software_factory.flows import address
from workflows.software_factory.stages import (
    ci,
    conflicts,
    copilot,
    pacing,
    pull_requests,
    readiness,
    workspaces,
)

from .policy import (
    TRUST,
    Blocked,
    address_subject,
    ci_subject,
    fingerprint,
    merge_subject,
)
from .settings import Settings

NAME = "pr_babysit"

__all__ = ["HANDED_OFF_ERRORS", "NAME", "pr_babysit"]

# What the Workflow hands off as blocked inside a Stage; the entry point hands off the same set when it escapes the run.
HANDED_OFF_ERRORS: tuple[type[Exception], ...] = (
    Blocked,
    WorktreeError,
    CalledProcessError,
    TimeoutExpired,
    OSError,
    ValueError,
)


def pr_babysit(settings: Settings, verify: Verify) -> Workflow[PullRequestWork]:
    """Poll until closed, repairing conflicts before feedback or CI; `settings.timeout` bounds agents and checks."""
    block = handoff(pull_requests.block_pr(fingerprint=fingerprint))
    fix_conflicts = (
        Flow("fix-conflicts")
        .within(
            workspaces.pr_worktree(),
            Flow("merge")
            .stage(conflicts.plan().on_fail(block))
            .stage(conflicts.prepare().on_fail(block))
            .stage(
                conflicts.resolve().tune(timeout_sec=settings.timeout).on_fail(block)
            )
            .stage(conflicts.verify(checks=verify).on_fail(block))
            .stage(conflicts.publish(message=merge_subject).on_fail(block)),
        )
        .stage(pacing.pace(settings.interval).named("pace-merge"))
    )
    maintain_ci = (
        Flow("maintain-ci")
        .within(
            workspaces.pr_worktree(),
            Flow("ci")
            .stage(workspaces.require_clean().named("require-clean-ci").on_hold(block))
            .stage(
                ci.diagnose()
                .tune(timeout_sec=settings.timeout)
                .on_hold(block)
                .on_fail(block)
            )
            .dispatch(
                ci.Diagnosis,
                {
                    ci.Transient: Flow("rerun").stage(
                        ci.rerun_failed()
                        .limit(settings.retries, per=ci.head)
                        .on_fail(block)
                    ),
                    ci.BranchBroken: (
                        Flow("fix")
                        .stage(
                            ci.fix(message=ci_subject, verify=verify)
                            .tune(timeout_sec=settings.timeout)
                            .on_fail(block)
                        )
                        .stage(
                            pull_requests.publish_batch()
                            .named("publish-ci-fix")
                            .reads(CiFixed, from_="fix-ci")
                            .on_fail(block)
                        )
                    ),
                },
            ),
        )
        .stage(pacing.pace(settings.interval).named("pace-ci"))
    )
    return (
        Workflow(NAME)
        .context(PullRequestWork)
        .on_error(HANDED_OFF_ERRORS, block)
        .stage(pull_requests.validate_origin().on_hold(block))
        .each(
            pacing.polls(),
            Flow("poll")
            .stage(pacing.start())
            .stage(pull_requests.snapshot())
            .stage(
                pull_requests.decide_next(
                    trust=TRUST,
                    author=settings.author or None,
                    copilot=lambda _: settings.copilot_review,
                )
            )
            .dispatch(
                NextAction,
                {
                    # A hold ends the poll loop, which is how a closed pull request completes the run.
                    PullRequestClosed: stop.held("the pull request is closed"),
                    Unsupported: block,
                    MergeConflict: fix_conflicts,
                    FeedbackBatch: address.address_feedback(
                        message=address_subject,
                        verify=verify,
                        on_baseline_red=reroute(CiFailing),
                        on_stuck=block,
                        agent_timeout=settings.timeout,
                    ),
                    CiFailing: maintain_ci,
                    CopilotDue: Flow("copilot").stage(copilot.request_review()),
                    ReadinessCheck: Flow("ready")
                    .stage(readiness.report())
                    .stage(pacing.pace(settings.interval).named("pace-ready")),
                },
            ),
            # A hand-off inside a poll ends the poll handed off; the loop turns it into the run's own, which records the same fingerprint again.
            on_fail=block,
            on_hold=stop.completed(),
        )
    )

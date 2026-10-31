"""Observe Jenkins failures, diagnose them read-only, and admit bounded recovery."""

from __future__ import annotations

from dataclasses import dataclass

from loop import Evidence, Fail, Hold, Pass, ReadOnly, Stage, Verdict
from workflows.software_factory.agents.roles.maintainer import (
    DIAGNOSE_JENKINS,
    JenkinsDiagnoseRequest,
    JenkinsDiagnosis,
)
from workflows.software_factory.events.ci import BranchBroken, Diagnosis, Transient
from workflows.software_factory.events.jenkins import (
    JenkinsCapacity,
    JenkinsFailed,
    JenkinsObservation,
    JenkinsReadiness,
    JenkinsRestarted,
    JenkinsWaiting,
    OtherCi,
)
from workflows.software_factory.events.pull_requests import PullRequestPolled
from workflows.software_factory.infrastructure.github.account import GitHub
from workflows.software_factory.infrastructure.github.pull_requests.babysit import (
    ci_state,
)
from workflows.software_factory.infrastructure.jenkins import (
    Jenkins,
    JenkinsBusy,
    JenkinsError,
    JenkinsFailure,
)


@dataclass(frozen=True)
class ObserveInput:
    polled: PullRequestPolled
    jenkins: Jenkins


@dataclass(frozen=True)
class DiagnoseInput:
    polled: PullRequestPolled
    failed: JenkinsFailed


@dataclass(frozen=True)
class RecoverInput:
    polled: PullRequestPolled
    failed: JenkinsFailed
    jenkins: Jenkins
    github: GitHub


def observe() -> Stage[ObserveInput, JenkinsObservation, JenkinsObservation]:
    """Select Jenkins before Actions, without clearing any required check."""
    return (
        Stage("observe-jenkins", ObserveInput, produces=JenkinsObservation)
        .action(_observe)
        .gate(lambda _, reply, __: Pass(reply))
    )


def diagnose() -> Stage[DiagnoseInput, JenkinsDiagnosis, Diagnosis]:
    """Classify a distinct failed Jenkins build using the read-only maintainer."""
    return (
        Stage("diagnose-jenkins", DiagnoseInput, produces=Diagnosis)
        .agent(
            DIAGNOSE_JENKINS,
            request=lambda i, _: JenkinsDiagnoseRequest(
                i.polled.snapshot,
                i.failed.logs,
                i.failed.job,
                i.failed.number,
                i.failed.result,
            ),
        )
        .gate(_diagnosed)
        .contract(ReadOnly())
        .revision(lambda i: i.failed.signal)
        .once(per=lambda i: i.failed.signal)
    )


def restart() -> Stage[RecoverInput, JenkinsRestarted, JenkinsRestarted]:
    """Submit once with durable provider reconciliation, even after interrupted effects."""
    return (
        Stage("restart-jenkins", RecoverInput, produces=JenkinsRestarted)
        .effect(_restart, reconcile=_restart)
        .gate(lambda _, reply, __: Pass(reply))
        .revision(lambda i: i.failed.signal)
    )


def capacity() -> Stage[RecoverInput, JenkinsCapacity, JenkinsCapacity]:
    """Recheck current head and capacity before fixing or publishing a Jenkins fix."""
    return (
        Stage("jenkins-capacity", RecoverInput, produces=JenkinsCapacity)
        .action(_capacity)
        .gate(lambda _, reply, __: Pass(reply))
    )


def readiness() -> Stage[ObserveInput, str, JenkinsReadiness]:
    """Prevent readiness before a tracked Jenkins job builds the new head."""
    return (
        Stage("jenkins-readiness", ObserveInput, produces=JenkinsReadiness)
        .action(
            lambda i: i.jenkins.readiness(
                i.polled.target.slug,
                i.polled.number,
                i.polled.head,
                i.polled.snapshot,
            )
        )
        .gate(_readiness)
    )


def _readiness(_: ObserveInput, result: str, __: Evidence) -> Verdict[JenkinsReadiness]:
    if result == "failed":
        return Fail("Tracked Jenkins job failed on this head")
    if result == "waiting":
        return Pass(JenkinsWaiting())
    if result == "green":
        return Pass(OtherCi())
    return Hold("Invalid Jenkins readiness result")


def _observe(i: ObserveInput) -> JenkinsObservation:
    jobs = i.jenkins.jobs_for(i.polled.target.slug, i.polled.number, i.polled.snapshot)
    if not jobs:
        return OtherCi()
    try:
        failures = i.jenkins.inspect(
            i.polled.target.slug, i.polled.number, i.polled.head, jobs
        )
    except JenkinsBusy:
        return JenkinsWaiting()
    if not failures:
        return OtherCi() if ci_state(i.polled.snapshot)["runs"] else JenkinsWaiting()
    failure = failures[0]
    return JenkinsFailed(
        failure.job,
        failure.number,
        failure.head,
        failure.result,
        failure.attempts,
        failure.logs,
        jobs,
    )


def _diagnosed(
    i: DiagnoseInput, reply: JenkinsDiagnosis, _: Evidence
) -> Verdict[Diagnosis]:
    reason = (
        f"Jenkins {i.failed.job}{i.failed.number}/: {reply.category}: {reply.reason}"
    )
    if reply.classification == "transient":
        return Pass(Transient(reason))
    if reply.classification == "branch":
        return Pass(BranchBroken(f"{reason}\n\n{i.failed.logs}"))
    return Hold(reason)


def _current_head(i: RecoverInput) -> str:
    pull = i.github.pull_requests(i.polled.target.slug).pull_request(i.polled.number)
    if not pull.is_open:
        raise JenkinsError("PR closed before Jenkins recovery")
    return pull.head_sha


def _restart(i: RecoverInput) -> JenkinsRestarted:
    failed = i.failed
    try:
        queue = i.jenkins.restart(
            i.polled.target.slug,
            i.polled.number,
            JenkinsFailure(
                failed.job,
                failed.number,
                failed.head,
                failed.result,
                failed.attempts,
                failed.logs,
            ),
            lambda: _current_head(i),
            failed.jobs,
        )
    except JenkinsBusy:
        queue = None
    return JenkinsRestarted(failed.head, failed.job, failed.number, queue)


def _capacity(i: RecoverInput) -> JenkinsCapacity:
    if _current_head(i) != i.failed.head:
        raise JenkinsError("PR head changed before Jenkins fix publication")
    failures = i.jenkins.inspect(
        i.polled.target.slug, i.polled.number, i.failed.head, i.failed.jobs
    )
    if not any(
        failure.job == i.failed.job and failure.number == i.failed.number
        for failure in failures
    ):
        raise JenkinsError(
            "Jenkins build changed before fix; poll again before editing"
        )
    return JenkinsCapacity()

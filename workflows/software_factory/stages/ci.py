"""The CI Stages: diagnose a failing head with the read-only maintainer, rerun its failed jobs, and fix the branch with the maintainer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from loop import (
    Evidence,
    Fail,
    HarnessCommits,
    History,
    Hold,
    Pass,
    ReadOnly,
    Retain,
    Stage,
    Verdict,
    Verify,
)
from loop.shared.github.account import GitHub
from loop.shared.github.pull_requests.babysit import ci_state, digest

from workflows.software_factory.agents.roles.maintainer import (
    DIAGNOSE_CI,
    FIX_CI,
    DiagnoseRequest,
    Fixed,
    FixRequest,
)
from workflows.software_factory.agents.roles.maintainer import (
    Diagnosis as Classification,
)
from workflows.software_factory.events.ci import (
    BranchBroken,
    CiFixed,
    CiRerun,
    Diagnosis,
    Transient,
)
from workflows.software_factory.events.pull_requests import PullRequestPolled

__all__ = [
    "BranchBroken",
    "Diagnosis",
    "Transient",
    "diagnose",
    "fix",
    "head",
    "rerun_failed",
]

_NO_LOGS = "No Actions run logs; inspect check/status links."


@dataclass(frozen=True)
class DiagnoseInput:
    polled: PullRequestPolled
    github: GitHub


@dataclass(frozen=True)
class RerunInput:
    polled: PullRequestPolled
    reruns: History[CiRerun]
    github: GitHub


@dataclass(frozen=True)
class FixInput:
    polled: PullRequestPolled
    broken: BranchBroken


def head(i: DiagnoseInput | RerunInput | FixInput) -> str:
    """The pull request head a CI Stage works on, the key a Workflow limits reruns by."""
    return i.polled.head


def signal(i: DiagnoseInput | RerunInput) -> str:
    """The head and the failed workflow runs with their attempt numbers, which changes when a rerun finishes failing again."""
    runs = ci_state(i.polled.snapshot)["runs"]
    return f"{i.polled.head}:{digest([(run['id'], run.get('run_attempt', 1)) for run in runs])}"


def diagnose() -> Stage[DiagnoseInput, Classification, Diagnosis]:
    """Read-only maintainer agent in diagnose mode classifies the failure as transient or branch-related; anything else holds. Once per head and CI-run signal across runs, so a diagnosis is replayed while CI is still being waited on."""
    return (
        Stage("diagnose-ci", DiagnoseInput, produces=Diagnosis)
        .agent(
            DIAGNOSE_CI,
            request=lambda i, _: DiagnoseRequest(i.polled.snapshot, _logs(i)),
        )
        .gate(_diagnosed)
        .contract(ReadOnly())
        .revision(signal)
        .once(per=signal)
    )


def rerun_failed() -> Stage[RerunInput, CiRerun, CiRerun]:
    """Reruns the failed jobs of every failed workflow run of the head; skipped while CI is pending or the same failure was already rerun, and a head with no failed run fails. A Workflow caps it with `.limit(n, per=head)`, which counts a started attempt before its effect, so a crash can under-use the cap but never exceed it."""
    return (
        Stage("rerun-failed", RerunInput, produces=CiRerun)
        .effect(_rerun, reconcile=lambda _: None)
        .gate(_reran)
        .when(_rerun_due)
    )


def fix(
    *, message: Callable[[FixInput], str], verify: Verify
) -> Stage[FixInput, Fixed, CiFixed]:
    """Maintainer agent in fix mode repairs the branch; a fix that changes nothing or is not reported as fixed fails, the runtime verifies then commits with `message(input)`, and a failed attempt keeps its worktree."""
    return (
        Stage("fix-ci", FixInput, produces=CiFixed)
        .agent(
            FIX_CI,
            request=lambda i, _: FixRequest(
                Classification("branch", i.broken.reason), i.polled.snapshot
            ),
        )
        .gate(_fixed)
        .contract(
            HarnessCommits(
                message=lambda i, _: message(i), verify=verify, on_fail=Retain()
            )
        )
    )


def _logs(i: DiagnoseInput) -> str:
    runs = ci_state(i.polled.snapshot)["runs"]
    if not runs:
        return _NO_LOGS
    return i.github.pull_requests(i.polled.target.slug).failed_logs(
        run["id"] for run in runs
    )


def _diagnosed(
    _: DiagnoseInput, reply: Classification, __: Evidence
) -> Verdict[Diagnosis]:
    match reply.classification:
        case "transient":
            return Pass(Transient(reply.reason))
        case "branch":
            return Pass(BranchBroken(reply.reason))
    return Hold(reply.reason or "CI diagnosis is unclear")


def _rerun_due(i: RerunInput) -> bool:
    """CI is not pending, and this failure, by head and signal, was not rerun yet."""
    snapshot = i.polled.snapshot
    return not ci_state(snapshot)["pending"] and not any(
        event.signal == signal(i) for event in i.reruns
    )


def _rerun(i: RerunInput) -> CiRerun:
    runs = tuple(run["id"] for run in ci_state(i.polled.snapshot)["runs"])
    pulls = i.github.pull_requests(i.polled.target.slug)
    for run_id in runs:
        pulls.rerun_failed(run_id)
    return CiRerun(i.polled.head, signal(i), runs)


def _reran(_: RerunInput, rerun: CiRerun, __: Evidence) -> Verdict[CiRerun]:
    return Pass(rerun) if rerun.runs else Fail("No failed workflow run to rerun")


def _fixed(i: FixInput, reply: Fixed, evidence: Evidence) -> Verdict[CiFixed]:
    if reply.verdict != "fixed":
        return Fail(reply.reason or "Maintainer could not fix CI")
    if not evidence.changed:
        return Fail("Maintainer claimed a fix but produced no changes")
    return Pass(CiFixed(i.polled.head, reply.reason))

"""The readiness Stage: report that a pull request head is, or is not, ready, once per change."""

from __future__ import annotations

from dataclasses import dataclass

from loop import Evidence, Pass, Stage, Verdict

from workflows.software_factory.events.pull_requests import (
    ReadinessCheck,
    ReadinessReported,
)


@dataclass(frozen=True)
class ReportInput:
    check: ReadinessCheck

    @property
    def head(self) -> str:
        """The pull request head the readiness is about."""
        return self.check.head

    @property
    def ready(self) -> bool:
        """Whether the pull request is ready to merge."""
        return self.check.ready


def report() -> Stage[ReportInput, ReadinessReported, ReadinessReported]:
    """Reports the readiness of the head, once per head and readiness across runs, so a poll that finds nothing new reports nothing; the report is the attempt's span, carrying `head` and `ready`."""
    return (
        Stage("report-readiness", ReportInput, produces=ReadinessReported)
        .effect(lambda i: ReadinessReported(i.head, i.ready), reconcile=lambda _: None)
        .gate(_reported)
        .once(per=lambda i: f"{i.head}:{i.ready}")
        .attributes(lambda i: {"head": i.head, "ready": i.ready})
    )


def _reported(
    _: ReportInput, reported: ReadinessReported, __: Evidence
) -> Verdict[ReadinessReported]:
    return Pass(reported)

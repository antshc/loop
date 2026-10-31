"""implement_spec's intake: which Specs a run takes up, and which held work a person has released."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from loop import Ledger
from loop.shared.github.issues.client import Issues
from loop.shared.github.issues.graph import SpecGraphSnapshot

from .policy import SpecWork, release_reason, spec_revision

logger = logging.getLogger("workflow.implement_spec")


@dataclass(frozen=True)
class Selection:
    """The open Specs of one intake: those to run, those that were not read completely and those that await a person."""

    runnable: tuple[SpecGraphSnapshot, ...]
    unreadable: tuple[SpecGraphSnapshot, ...]
    awaiting_human: tuple[SpecGraphSnapshot, ...]


def select_specs(tracker: Issues, number: int | None = None) -> Selection:
    """The open Specs (only `number` when given), split by whether a run may act on them."""
    runnable: list[SpecGraphSnapshot] = []
    unreadable: list[SpecGraphSnapshot] = []
    waiting: list[SpecGraphSnapshot] = []
    for spec in tracker.specs(number):
        if not spec.complete:
            logger.warning(
                "Spec #%s was not read completely; no decision is made from it: %s",
                spec.number,
                spec.gaps,
            )
            unreadable.append(spec)
        elif spec.awaiting_human:
            waiting.append(spec)
        else:
            runnable.append(spec)
    return Selection(tuple(runnable), tuple(unreadable), tuple(waiting))


def work_of(spec: SpecGraphSnapshot) -> SpecWork:
    """The work item of a Spec: its number and the revision of its body."""
    return SpecWork(spec.number, spec_revision(spec))


def release_held(ledger: Ledger, workflow: str, spec: SpecGraphSnapshot) -> str | None:
    """Writes a release when the ledger shows work held for a person and the `hitl` label that held it is gone; returns the reason."""
    work = work_of(spec)
    reason = release_reason(ledger.status(workflow, work.id), spec)
    if reason is not None:
        ledger.release(workflow, work.id, reason)
        logger.info("Spec #%s released: %s", spec.number, reason)
    return reason

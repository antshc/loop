"""implement_spec's intake and policy: which Specs a run takes up, and when held work counts as released."""

from __future__ import annotations

import pytest

from software_factory.events.specs import SpecBlocked, SpecHandedToHuman
from software_factory.events.tickets import TicketEscalated
from software_factory.runtime import RunStatus
from software_factory.shared.github.issues.client import Issues
from software_factory.shared.github.repo import GitHubRepo
from software_factory.tests.shared.builders import build_graph, build_ticket
from software_factory.tests.shared.fakes import FakeGhCli, issue
from software_factory.workflows.implement_spec.intake import select_specs, work_of
from software_factory.workflows.implement_spec.policy import (
    HELD_SPEC, HELD_TICKET, HITL, chore_subject, held_by_spec, held_by_ticket, release_reason, spec_revision,
)

LABELS = ("spec", "repo:target:owner/repo", "repo:base:main")

HELD_BY_TICKET = {HELD_SPEC: True, HELD_TICKET: 10}


def status(kind: str = "failed", attributes: dict | None = None, *, released: bool = False) -> RunStatus:
    """The latest run of a work item as the ledger reports it."""
    return RunStatus(1, kind, None, attributes or {}, released)  # type: ignore[arg-type]


def spec_with(labels: tuple[str, ...] = ("spec",), *, ticket_labels: tuple[str, ...] = ()):
    """A Spec graph with one open Ticket."""
    return build_graph(labels=labels, tickets=(build_ticket(10, labels=ticket_labels),))


def test_the_intake_splits_the_open_specs_into_runnable_unreadable_and_awaiting_a_person() -> None:
    """Given three open Specs, when the intake selects, then each lands in the group that says what a run may do with it."""
    gh = FakeGhCli(
        specs=[issue(1, "Checkout: Ready", labels=LABELS), issue(2, "Checkout: Waiting", labels=(*LABELS, HITL)), issue(3, "Checkout: Torn", labels=LABELS)],
        tickets={1: [issue(10, "Form")], 2: [issue(11, "Other")], 3: [issue(12, "Torn")]},
    )
    gh.fail_on("api", "--paginate", "--slurp", "repos/owner/repo/issues/3/sub_issues?per_page=100")

    selection = select_specs(Issues(GitHubRepo("owner", "repo", gh=gh)))

    assert [spec.number for spec in selection.runnable] == [1]
    assert [spec.number for spec in selection.awaiting_human] == [2]
    assert [spec.number for spec in selection.unreadable] == [3]


def test_the_intake_selects_only_the_asked_for_spec() -> None:
    """Given two open Specs, when the intake is asked for one number, then only that Spec is selected."""
    gh = FakeGhCli(specs=[issue(1, "Checkout: A", labels=LABELS), issue(2, "Checkout: B", labels=LABELS)], tickets={1: [], 2: []})

    selection = select_specs(Issues(GitHubRepo("owner", "repo", gh=gh)), 2)

    assert [spec.number for spec in selection.runnable] == [2]


def test_the_work_item_of_a_spec_is_its_number_and_a_digest_of_its_body() -> None:
    """Given a Spec, when its work item is made, then the id is spec-<number> and the revision follows the body."""
    spec = build_graph(number=7)

    work = work_of(spec)

    assert work.id == "spec-7" and work.revision == spec_revision(spec) and len(work.revision) == 64
    assert spec_revision(build_graph(number=7)) == work.revision


@pytest.mark.parametrize(
    "current",
    [status("failed", HELD_BY_TICKET), status("handed_off", {HELD_SPEC: True})],
)
def test_held_work_is_released_once_the_labels_that_held_it_are_gone(current: RunStatus) -> None:
    """Given a run that ended with hand-offs recorded, when the labels they set are gone, then the work is released."""
    assert release_reason(current, spec_with()) == "hitl was removed"


@pytest.mark.parametrize(
    ("current", "spec"),
    [
        (None, spec_with()),
        (status("completed", HELD_BY_TICKET), spec_with()),
        (status("failed", {}), spec_with()),
        (status("failed", HELD_BY_TICKET, released=True), spec_with()),
        (status("failed", HELD_BY_TICKET), spec_with((HITL, "spec"))),
        (status("failed", HELD_BY_TICKET), spec_with(ticket_labels=(HITL,))),
    ],
)
def test_work_is_not_released_without_a_hand_off_or_while_hitl_remains(current: RunStatus | None, spec) -> None:
    """Given no run, a clean run, no recorded hand-off, an earlier release, or hitl still on the Spec or the held Ticket, then nothing is released."""
    assert release_reason(current, spec) is None


def test_a_hand_off_that_labelled_nothing_records_nothing() -> None:
    """Given a block that found no work to hold back, when its run attributes are read, then none are recorded."""
    assert held_by_spec(SpecBlocked(HITL, "m", labelled=False), None) == {}
    assert held_by_spec(SpecBlocked(HITL, "m", labelled=True), None) == {HELD_SPEC: True}
    assert held_by_spec(SpecHandedToHuman(HITL, "m"), None) == {HELD_SPEC: True}


def test_an_escalation_records_the_ticket_and_the_spec() -> None:
    """Given an escalated Ticket, when its run attributes are read, then both the Spec and the Ticket are recorded as held."""
    assert held_by_ticket(TicketEscalated(10, HITL, "why"), "why") == HELD_BY_TICKET


def test_the_chore_subject_is_the_stage_librarys_subject_for_the_initiative() -> None:
    """Given an initiative and a proposed message, when the policy builds the subject, then it carries the chore prefix and the message."""
    assert chore_subject("Checkout", "tidy the form") == "chore(Checkout): tidy the form"

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from workflows.shared.github.issues.issue import IssueState
from workflows.shared.github.issues.spec import SpecMetadata
from workflows.tests.shared.builders import build_spec, build_ticket


def _metadata(title: str = "Add login page", labels: tuple[str, ...] = (), number: int = 7) -> SpecMetadata:
    return SpecMetadata.of(number, title, labels)


def test_initiative_and_bare_title_split_on_the_first_colon() -> None:
    metadata = _metadata("Checkout: Add login page")

    assert (metadata.initiative, metadata.bare_title) == ("Checkout", "Add login page")


def test_initiative_falls_back_to_the_number_without_a_colon_prefix() -> None:
    metadata = _metadata("Add login page")

    assert (metadata.initiative, metadata.bare_title) == ("7", "Add login page")


def test_target_reads_the_single_repo_target_value() -> None:
    metadata = _metadata(labels=("spec", "repo:target:owner/name"))

    assert metadata.target == "owner/name"
    assert metadata.target_label == "repo:target:owner/name"


@pytest.mark.parametrize(
    "labels",
    [
        (),
        ("repo:target:not-a-slug",),
        ("repo:target:owner/name", "repo:target:owner/other"),
        ("repo:target:github.com/owner/name",),
    ],
)
def test_target_is_none_when_missing_malformed_or_duplicated(labels: tuple[str, ...]) -> None:
    metadata = _metadata(labels=labels)

    assert metadata.target is None and metadata.target_label is None


def test_base_branch_reads_the_single_repo_base_value() -> None:
    assert _metadata(labels=("repo:base:main",)).base_branch == "main"


@pytest.mark.parametrize("labels", [(), ("repo:base:",), ("repo:base:a", "repo:base:b")])
def test_base_branch_is_none_when_missing_empty_or_duplicated(labels: tuple[str, ...]) -> None:
    assert _metadata(labels=labels).base_branch is None


def test_a_spec_exposes_its_metadata_from_its_title_and_routing_labels() -> None:
    spec = build_spec(7, "Checkout: Add login page", ("spec", "repo:target:o/r", "repo:base:release/2.4"))

    assert spec.metadata == SpecMetadata("Checkout", "Add login page", "o/r", "release/2.4")


@pytest.mark.parametrize(
    ("labels", "state", "actionable"),
    [
        ((), IssueState.OPEN, True),
        (("bug",), IssueState.OPEN, True),
        (("hitl",), IssueState.OPEN, False),
        (("HITL",), IssueState.OPEN, False),
        (("spec",), IssueState.OPEN, False),
        ((), IssueState.CLOSED, False),
    ],
)
def test_a_ticket_is_actionable_when_open_and_neither_hitl_nor_spec(
    labels: tuple[str, ...], state: IssueState, actionable: bool
) -> None:
    assert build_ticket(labels=labels, state=state).actionable is actionable


def test_a_spec_and_a_ticket_are_immutable_snapshots() -> None:
    spec = build_spec()
    ticket = build_ticket()

    with pytest.raises(FrozenInstanceError):
        setattr(spec, "labels", ("hitl",))
    with pytest.raises(FrozenInstanceError):
        setattr(ticket, "state", IssueState.CLOSED)


def test_changing_a_snapshot_returns_a_new_one_of_the_same_kind() -> None:
    ticket = build_ticket()

    labeled, closed = ticket.with_label("hitl"), ticket.closed()

    assert ("hitl" in labeled.labels, ticket.labels, type(labeled)) == (True, (), type(ticket))
    assert closed.state == IssueState.CLOSED and ticket.state == IssueState.OPEN

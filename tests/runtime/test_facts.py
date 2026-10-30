"""Stage values and input resolution: field kinds, closed sums, and what a Stage refuses at construction."""

from __future__ import annotations

from dataclasses import dataclass, fields

import pytest

from loop import All, DefinitionError, ReadOnly, Stage, handoff, rework
from loop.runtime.facts import (
    Fact, FieldKind, closed_sum, event_shape, input_fields, merge, place_plain, producers_of,
)

from .support import (
    Alpha, Beta, Gamma, NeedsAllRouted, NeedsChore, NeedsClient, NeedsHistory, NeedsOptionalRouted, NeedsRouted,
    NeedsSpec, NeedsTicket, NeedsWorkspace, NoInput, NotADataclass, Outcome, PrimitiveInput, Routed, Spec, Ticket, Triple,
    Unfrozen, agent, effect, make,
)


@dataclass(frozen=True)
class BareMarker:
    routed: All


@dataclass(frozen=True)
class OptionalPrimitive:
    note: str | None


@dataclass(frozen=True)
class MarkerOfPrimitive:
    note: All[str]  # type: ignore[type-var]


@dataclass(frozen=True)
class UntypedCallable:
    handler: object


@dataclass(frozen=True)
class Unresolvable:
    thing: Missing  # type: ignore[name-defined]  # noqa: F821


@pytest.mark.parametrize(
    ("input_type", "kind"),
    [
        (NeedsRouted, FieldKind.EVENT),
        (NeedsOptionalRouted, FieldKind.OPTIONAL),
        (NeedsAllRouted, FieldKind.ALL),
        (NeedsHistory, FieldKind.HISTORY),
        (NeedsWorkspace, FieldKind.WORKSPACE),
        (NeedsChore, FieldKind.OPTIONAL),
        (NeedsSpec, FieldKind.PLAIN),
    ],
)
def test_each_annotation_resolves_to_its_own_field_kind(input_type, kind) -> None:
    """Given an input field annotation, when the input is classified, then the field has the kind its annotation names."""
    (classified,) = input_fields("Stage 's'", input_type)

    assert classified.kind is kind


def test_an_optional_sum_field_reads_every_variant_of_the_sum() -> None:
    """Given a `Chored | Discarded | None` field, when classified, then its shape holds both Events."""
    (classified,) = input_fields("Stage 's'", NeedsChore)

    assert {event.__name__ for event in classified.shape} == {"Chored", "Discarded"}


def test_a_plain_field_resolves_to_the_nearest_loop_item_then_the_context_then_a_service() -> None:
    """Given item, context and service fields, when placed, then each resolves by its type."""
    (ticket,) = input_fields("s", NeedsTicket)
    (spec,) = input_fields("s", NeedsSpec)
    (client,) = input_fields("s", NeedsClient)

    assert place_plain(ticket, Spec, (Ticket,)) is FieldKind.ITEM
    assert place_plain(spec, Spec, (Ticket,)) is FieldKind.CONTEXT
    assert place_plain(spec, None, ()) is FieldKind.SERVICE
    assert place_plain(client, Spec, (Ticket,)) is FieldKind.SERVICE
    assert place_plain(ticket, Ticket, (Ticket,)) is FieldKind.ITEM


@pytest.mark.parametrize(
    ("input_type", "message"),
    [
        (PrimitiveInput, "field 'count': .*primitives and untyped values are rejected"),
        (UntypedCallable, "field 'handler'"),
        (OptionalPrimitive, "only an Event can be optional"),
        (BareMarker, "All needs an Event type argument"),
        (MarkerOfPrimitive, "reads Events, not"),
        (NotADataclass, "must be a frozen dataclass"),
        (Unfrozen, "must be a frozen dataclass"),
        (Unresolvable, "unresolvable annotation"),
    ],
    ids=["primitive", "object", "optional-primitive", "bare-marker", "marker-of-primitive", "not-dataclass", "unfrozen", "unresolvable"],
)
def test_an_unusable_input_is_rejected_when_the_stage_is_built(input_type, message) -> None:
    """Given an unusable input type, when a Stage is built over it, then the error names the Stage and the field."""
    with pytest.raises(DefinitionError, match=f"Stage 'bad'.*{message}"):
        Stage("bad", input_type, Routed)


@pytest.mark.parametrize("identity", ["Bad", "snake_case", "-lead", "trail-", "double--dash", "", "1st"])
def test_a_stage_identity_must_be_kebab_case(identity) -> None:
    """Given a non-kebab-case identity, when a Stage is built, then it is rejected."""
    with pytest.raises(DefinitionError, match="identity must be kebab-case"):
        Stage(identity, NoInput, Routed)


def test_a_stage_must_declare_the_event_it_produces() -> None:
    """Given a produced type that is not an Event, when a Stage is built, then it is rejected."""
    with pytest.raises(DefinitionError, match="produces must be an Event or a closed sum of Events"):
        Stage("bad", NoInput, int)


def test_a_stage_may_produce_a_closed_sum() -> None:
    """Given a type alias of Events, when a Stage is built over it, then it is accepted."""
    assert Stage("decide", NoInput, Outcome).produces is Outcome


def test_may_emit_lists_events_only() -> None:
    """Given a non-Event in may_emit, when a Stage is built, then it is rejected."""
    with pytest.raises(DefinitionError, match="may_emit lists Events"):
        Stage("bad", NoInput, Routed, may_emit=(int,))  # type: ignore[arg-type]


def test_a_route_must_be_a_route_value() -> None:
    """Given a non-route in a route position, when a Stage is built, then it is rejected."""
    with pytest.raises(DefinitionError, match="is not a route"):
        make("bad").on_fail("stop")  # type: ignore[arg-type]


def test_every_modifier_returns_a_modified_copy_and_leaves_the_original() -> None:
    """Given a Stage, when each modifier is applied, then a different Stage is returned and the original is unchanged."""
    original = effect("push")
    before = {field.name: getattr(original, field.name) for field in fields(original)}
    modified = [
        original.on_pass(rework(max_attempts=2)), original.on_fail(handoff(effect("hand"))), original.on_hold(rework(max_attempts=1)),
        original.when(lambda _: True), original.revision(lambda _: "r"), original.limit(2, per=lambda _: "k"),
        original.once(per=lambda _: "k"), original.named("push-later"), original.reads(Routed, from_="x"),
        original.attributes(lambda _: {}), original.contract(ReadOnly()), original.gate(lambda *_: None),
    ]

    assert all(copy is not original and copy != original for copy in modified)
    assert {field.name: getattr(original, field.name) for field in fields(original)} == before


def test_tuning_applies_to_an_agent_stage_only() -> None:
    """Given a non-agent Stage, when it is tuned, then it is rejected; an agent Stage gets a tuned copy."""
    with pytest.raises(DefinitionError, match="Stage 'push': tune applies to an agent Stage only"):
        effect("push").tune(timeout_sec=1)
    assert agent("dev").tune(timeout_sec=1).executor.agent.profile.timeout_sec == 1


def test_a_stage_has_at_most_one_executor() -> None:
    """Given a Stage with an executor, when another is added, then it is rejected."""
    with pytest.raises(DefinitionError, match="already has an executor"):
        effect("push").action(lambda _: None)


def test_a_renamed_copy_must_keep_a_kebab_case_identity() -> None:
    """Given a Stage, when it is renamed to a non-kebab-case identity, then it is rejected."""
    with pytest.raises(DefinitionError, match="kebab-case"):
        make("push").named("Push Later")


def test_event_shape_reads_events_sums_and_aliases() -> None:
    """Given annotations, when the Event shape is read, then Events, unions and aliases give their variants and others give none."""
    assert event_shape(Alpha) == {Alpha}
    assert event_shape(Alpha | Beta) == {Alpha, Beta}
    assert event_shape(Outcome) == {Alpha, Beta}
    assert event_shape(Alpha | int) is None
    assert event_shape(int) is None
    assert event_shape(Spec) is None


def test_a_closed_sum_has_at_least_two_variants() -> None:
    """Given a single Event and a sum, when closed sums are read, then only the sum counts."""
    assert closed_sum(Alpha) is None
    assert closed_sum(Triple) == {Alpha, Beta, Gamma}


def test_facts_after_paths_meet_are_definite_only_when_every_path_holds_them_definitely() -> None:
    """Given two paths where only one produced an Event, when they merge, then that Event is no longer definite."""
    always = Fact(frozenset({Alpha}), "a", True)
    sometimes = Fact(frozenset({Beta}), "b", True)

    merged = merge([(always, sometimes), (always,)])

    assert {(fact.producer, fact.definite) for fact in merged} == {("a", True), ("b", False)}


def test_a_fact_that_a_path_holds_only_maybe_is_maybe_after_the_merge() -> None:
    """Given one path with a maybe fact, when paths merge, then the fact is maybe."""
    definite = Fact(frozenset({Alpha}), "a", True)
    maybe = Fact(frozenset({Alpha}), "a", False)

    assert [fact.definite for fact in merge([(definite,), (maybe,)])] == [False]


def test_producers_are_distinct_and_ordered() -> None:
    """Given facts of one Event from two Stages, when producers are read, then each appears once in order."""
    facts = [Fact(frozenset({Alpha}), "x", True), Fact(frozenset({Alpha, Beta}), "y", False), Fact(frozenset({Alpha}), "x", False)]

    assert producers_of(facts, Alpha) == ("x", "y")
    assert producers_of(facts, Beta) == ("y",)
    assert producers_of(facts, Gamma) == ()

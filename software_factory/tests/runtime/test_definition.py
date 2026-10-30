"""Building a Workflow definition: reuse, identities, scopes, routes and switches are checked as the definition is built."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from software_factory.runtime import (
    All, DefinitionError, Flow, HarnessCommits, ReadOnly, Retain, Routes, Workflow, handoff, next_, next_item, reroute, rework,
    stop,
)

from .support import (
    Alpha, Beta, Chored, Client, Delivered, Gamma, NeedsAlpha, NeedsAllRouted, NeedsClient, NeedsDelivered, NeedsHistory,
    NeedsOptionalDelivered, NeedsPublished, NeedsRouted, NeedsSpec, NeedsTicket, NeedsWorkspace, Outcome, Published, Routed,
    Spec, Triple, agent, checkout, effect, make, tickets,
)


def workflow() -> Workflow[Spec]:
    return Workflow("w").context(Spec)


def routed() -> Workflow[Spec]:
    return workflow().stage(make("route-spec", produces=Routed))


def wiring(definition: Workflow[Spec]) -> list[str]:
    """The wiring table with its column padding collapsed."""
    return [" ".join(line.split()) for line in definition.explain().splitlines()]


def test_a_library_stage_overridden_in_a_definition_is_a_copy_and_the_library_stage_is_unchanged() -> None:
    """Given a library Stage, when a definition overrides routes, tuning and policy, then the library Stage is unchanged."""
    library = agent("implement-ticket")
    escalate = effect("escalate-ticket")

    placed = library.on_fail(rework(max_attempts=2, then=handoff(escalate))).tune(timeout_sec=7).once(per=lambda _: "k")
    definition = workflow().within(checkout(), Flow("ticket").stage(placed))

    assert library.routes == Routes()
    assert library.once_ is None
    assert library.executor.agent.profile.timeout_sec is None
    used = definition.nodes[0].body.nodes[0].stage
    assert used.routes.failed == rework(max_attempts=2, then=handoff(escalate))
    assert used.executor.agent.profile.timeout_sec == 7


def test_adding_a_node_leaves_the_earlier_flow_unchanged() -> None:
    """Given a flow, when another Stage is added, then the original flow still has its own nodes."""
    first = Flow("f").stage(make("one"))

    first.stage(make("two"))

    assert len(first.nodes) == 1


def test_the_same_stage_placed_twice_with_a_rename_has_two_stable_identities() -> None:
    """Given one Stage placed twice, when the second placement is renamed, then both are accepted under their own identity."""
    publish = effect("publish-draft", produces=Published)

    definition = routed().stage(publish).stage(publish.named("publish-later")).check()

    assert [node.stage.id for node in definition.nodes[1:]] == ["publish-draft", "publish-later"]


def test_the_same_stage_placed_twice_without_a_rename_is_rejected() -> None:
    """Given one Stage placed twice, when no rename is given, then the definition is rejected for a duplicate identity."""
    publish = effect("publish-draft", produces=Published)

    with pytest.raises(DefinitionError, match="Stage 'publish-draft': identity is used more than once"):
        routed().stage(publish).stage(publish)


def test_a_stage_reading_an_event_no_earlier_stage_produces_names_the_stage_and_the_field() -> None:
    """Given no producer of Routed, when a Stage reads it, then the error names the Stage, the field and the missing producer."""
    with pytest.raises(DefinitionError, match=r"Stage 'deliver' field 'routed': no earlier Stage produces Routed"):
        workflow().stage(make("deliver", NeedsRouted, Delivered))


def test_a_producer_placed_after_its_reader_is_not_upstream() -> None:
    """Given the producer is placed later, when the reader is added first, then it is rejected."""
    with pytest.raises(DefinitionError, match="no earlier Stage produces Routed"):
        workflow().stage(make("deliver", NeedsRouted, Delivered)).stage(make("route-spec", produces=Routed))


def test_a_stage_reading_an_event_two_stages_produce_must_name_the_producer_and_the_error_names_every_producer() -> None:
    """Given two producers of Published, when a Stage reads it unnamed, then the error names both; naming one is accepted."""
    two = routed().stage(effect("publish-early", produces=Published)).stage(effect("publish-late", produces=Published))

    with pytest.raises(DefinitionError, match=r"field 'published': Published is produced by 'publish-early', 'publish-late'"):
        two.stage(make("finalize", NeedsPublished, Delivered))

    named = two.stage(make("finalize", NeedsPublished, Delivered).reads(Published, from_="publish-late")).check()
    assert wiring(named)[-1] == "finalize published event Published from publish-late"


def test_a_named_producer_that_does_not_produce_the_event_is_rejected() -> None:
    """Given a producer of another Event, when it is named for Published, then the error names the Stage, field and producer."""
    reader = make("finalize", NeedsPublished, Delivered).reads(Published, from_="route-spec")

    with pytest.raises(DefinitionError, match="Stage 'finalize' field 'published': producer 'route-spec' does not produce Published"):
        routed().stage(effect("publish", produces=Published)).stage(reader)


def test_a_named_producer_that_is_not_upstream_is_rejected() -> None:
    """Given a producer placed after the reader, when the reader names it, then the error says it is not upstream."""
    reader = make("finalize", NeedsPublished, Delivered).reads(Published, from_="publish-late")
    two = routed().stage(effect("publish-early", produces=Published)).stage(effect("publish-mid", produces=Published))

    with pytest.raises(DefinitionError, match=r"Stage 'finalize' field 'published': producer 'publish-late' is not upstream"):
        two.stage(reader)


def test_a_stage_that_names_a_producer_for_an_event_it_does_not_read_is_rejected() -> None:
    """Given a Stage with no Published field, when it names a producer for Published, then building fails."""
    reader = make("deliver", NeedsRouted, Delivered).reads(Published, from_="publish")

    with pytest.raises(DefinitionError, match="Stage 'deliver': reads Published from 'publish', but no input field reads Published"):
        routed().stage(effect("publish", produces=Published)).stage(reader)


@pytest.mark.parametrize(
    "stage",
    [
        agent("dev"),
        make("read").contract(ReadOnly()),
        make("space", NeedsWorkspace),
    ],
    ids=["agent", "repository-contract", "workspace-field"],
)
def test_a_stage_needing_the_workspace_outside_a_workspace_scope_is_rejected(stage) -> None:
    """Given a Stage that needs the workspace, when it is placed outside a within, then building fails naming the Stage."""
    with pytest.raises(DefinitionError, match=f"Stage '{stage.id}'.*outside a workspace scope"):
        workflow().stage(stage)


def test_a_stage_needing_the_workspace_is_accepted_inside_a_workspace_scope() -> None:
    """Given a workspace scope, when workspace Stages are placed inside it, then the definition is accepted."""
    body = Flow("body").stage(agent("dev")).stage(make("read").contract(ReadOnly())).stage(make("space", NeedsWorkspace))

    assert workflow().within(checkout(), body).check()


def test_a_stage_placed_after_the_workspace_scope_ends_is_rejected() -> None:
    """Given a finished workspace scope, when an agent Stage follows it, then building fails."""
    inside = workflow().within(checkout(), Flow("body").stage(make("first")))

    with pytest.raises(DefinitionError, match="Stage 'late': runs an agent but is placed outside a workspace scope"):
        inside.stage(agent("late"))


def test_a_workspace_scope_inside_another_ends_back_in_the_outer_scope() -> None:
    """Given nested scopes, when an agent Stage follows the inner one, then it still sits in the outer workspace."""
    inner = Flow("outer").within(checkout("inner"), Flow("inner").stage(agent("a"))).stage(agent("b"))

    assert workflow().within(checkout("outer"), inner).check()


def outcome_flow() -> Workflow[Spec]:
    return routed().stage(make("decide", produces=Outcome))


def test_a_switch_covering_every_variant_exactly_once_is_accepted() -> None:
    """Given a producer of the sum, when every variant has a case, then the definition is accepted."""
    cases = {Alpha: Flow("a").stage(make("on-alpha", NeedsAlpha, Delivered)), Beta: stop.completed()}

    assert outcome_flow().dispatch(Outcome, cases).check()


def test_a_switch_missing_a_variant_is_rejected() -> None:
    """Given a switch without a Beta case, when it is added, then building fails naming the missing variant."""
    with pytest.raises(DefinitionError, match="dispatch on Outcome: misses Beta"):
        outcome_flow().dispatch(Outcome, {Alpha: stop.completed()})


def test_a_switch_duplicating_a_variant_is_rejected() -> None:
    """Given a switch listing Alpha twice, when it is added, then building fails naming the duplicate."""
    cases = [(Alpha, stop.completed()), (Alpha, stop.idle()), (Beta, stop.completed())]

    with pytest.raises(DefinitionError, match="dispatch on Outcome: duplicates Alpha"):
        outcome_flow().dispatch(Outcome, cases)


def test_a_switch_with_a_case_that_is_not_a_variant_is_rejected() -> None:
    """Given a case for an Event outside the sum, when the switch is added, then building fails."""
    with pytest.raises(DefinitionError, match="covers non-variants Gamma"):
        outcome_flow().dispatch(Outcome, {Alpha: stop.completed(), Beta: stop.completed(), Gamma: stop.completed()})


def test_a_switch_on_something_that_is_not_a_closed_sum_is_rejected() -> None:
    """Given a single Event, when it is switched on, then building fails."""
    with pytest.raises(DefinitionError, match="not a closed sum"):
        outcome_flow().dispatch(Alpha, {Alpha: stop.completed()})


def test_a_switch_with_no_earlier_producer_of_any_variant_is_rejected() -> None:
    """Given no Stage producing a variant, when the switch is added, then building fails."""
    with pytest.raises(DefinitionError, match="no earlier Stage definitely produces any variant of Outcome"):
        routed().dispatch(Outcome, {Alpha: stop.completed(), Beta: stop.completed()})


def test_a_switch_whose_only_producer_may_be_skipped_is_rejected() -> None:
    """Given a conditional producer, when the switch follows it, then building fails because it may not produce."""
    conditional = routed().stage(make("decide", produces=Outcome).when(lambda _: True))

    with pytest.raises(DefinitionError, match="no earlier Stage definitely produces any variant"):
        conditional.dispatch(Outcome, {Alpha: stop.completed(), Beta: stop.completed()})


def test_a_case_sees_its_own_variant_as_produced_by_the_switched_producer() -> None:
    """Given a switch, when a case Stage reads its variant, then the wiring table names the deciding Stage."""
    cases = {Alpha: Flow("a").stage(make("on-alpha", NeedsAlpha, Delivered)), Beta: stop.completed()}

    assert "on-alpha alpha event Alpha from decide" in wiring(outcome_flow().dispatch(Outcome, cases))


def test_a_case_cannot_read_the_variant_of_a_sibling_case() -> None:
    """Given a Beta case, when its Stage reads Alpha, then the Stage reads no variant and building fails."""
    cases = {Alpha: stop.completed(), Beta: Flow("b").stage(make("on-beta", NeedsAlpha, Delivered))}

    with pytest.raises(DefinitionError, match="Stage 'on-beta' field 'alpha'"):
        outcome_flow().dispatch(Outcome, cases)


def test_a_stage_that_retains_a_failed_attempt_cannot_have_a_rework_route() -> None:
    """Given a Retain contract, when the Stage reworks, then building fails."""
    kept = make("fix").contract(HarnessCommits(lambda *_: "m", on_fail=Retain()))

    with pytest.raises(DefinitionError, match="Stage 'fix': retains a failed attempt for inspection, so it cannot have a rework route"):
        Flow("f").stage(kept.on_fail(rework(max_attempts=2)))


def test_a_stage_that_retains_a_failed_attempt_is_accepted_without_rework() -> None:
    """Given a Retain contract, when the Stage has no rework route, then it is accepted."""
    kept = make("fix").contract(HarnessCommits(lambda *_: "m", on_fail=Retain()))

    assert Flow("f").stage(kept.on_fail(stop.failed()))


def test_a_hold_route_that_asks_for_rework_is_rejected() -> None:
    """Given a hold route that reworks, when the Stage is added, then building fails."""
    with pytest.raises(DefinitionError, match="Stage 'wait': a hold route cannot ask for rework"):
        Flow("f").stage(make("wait").on_hold(rework(max_attempts=2)))


@pytest.mark.parametrize(
    ("stage", "message"),
    [
        (make("once-pure").once(per=lambda _: "k"), "has no executor"),
        (make("once-action").action(lambda _: None).once(per=lambda _: "k"), "has a deterministic action executor"),
        (effect("both").once(per=lambda _: "k").limit(2, per=lambda _: "k"), "either once or an at-most-N limit"),
        (effect("none").limit(0, per=lambda _: "k"), "at least 1 attempt"),
    ],
    ids=["no-executor", "action", "once-and-limit", "limit-below-one"],
)
def test_misplaced_once_and_limit_rules_are_rejected(stage, message) -> None:
    """Given a once or limit rule on a Stage that cannot take it, when it is added, then building fails naming the Stage."""
    with pytest.raises(DefinitionError, match=f"Stage '{stage.id}': .*{message}"):
        Flow("f").stage(stage)


def test_a_once_rule_on_an_effect_or_agent_stage_is_accepted() -> None:
    """Given effect and agent Stages, when once-per-key is set, then they are accepted."""
    body = Flow("b").stage(agent("dev").once(per=lambda _: "k"))

    assert workflow().stage(effect("push").once(per=lambda _: "k")).within(checkout(), body)


@pytest.mark.parametrize("bound", [0, -1])
def test_a_rework_bound_below_one_is_rejected(bound) -> None:
    """Given a rework route with a bound below 1, when the Stage is added, then building fails naming the Stage."""
    with pytest.raises(DefinitionError, match=f"Stage 'dev': the rework bound must be at least 1, not {bound}"):
        Flow("f").stage(make("dev").on_fail(rework(max_attempts=bound)))


def test_a_handoff_that_runs_an_agent_stage_is_rejected() -> None:
    """Given a hand-off to an agent Stage, when it is written on a route, then building fails."""
    with pytest.raises(DefinitionError, match="hand-off Stage 'escalate' runs an agent"):
        Flow("f").stage(make("dev").on_fail(handoff(agent("escalate"))))


def test_a_handoff_to_an_effect_stage_may_be_shared_by_several_routes() -> None:
    """Given one hand-off Stage, when several routes use it, then the definition is accepted once."""
    block = handoff(effect("block"))

    assert workflow().stage(make("one").on_fail(block)).stage(make("two").on_hold(block)).check()


def test_a_handoff_identity_colliding_with_a_placed_stage_is_rejected() -> None:
    """Given a placed Stage and a hand-off sharing an identity, when both are added, then building fails."""
    with pytest.raises(DefinitionError, match="hand-off Stage identity 'one' is already used"):
        workflow().stage(make("one")).stage(make("two").on_fail(handoff(effect("one"))))


def test_a_next_item_route_outside_a_loop_is_rejected() -> None:
    """Given a next-item route in no loop, when the Stage is added, then building fails."""
    with pytest.raises(DefinitionError, match="Stage 'done': a next-item route appears outside an each loop"):
        workflow().stage(make("done").on_pass(next_item()))


def test_a_next_item_route_inside_a_loop_is_accepted() -> None:
    """Given a loop body, when a Stage ends the item, then the definition is accepted."""
    body = Flow("ticket").stage(make("done", NeedsTicket).on_pass(next_item()))

    assert workflow().each(tickets(), body).check()


def test_an_each_loop_cannot_rework() -> None:
    """Given a loop whose on_fail reworks, when it is added, then building fails."""
    with pytest.raises(DefinitionError, match="an each loop cannot rework"):
        workflow().each(tickets(), Flow("ticket"), on_fail=rework(max_attempts=2))


def test_events_produced_inside_a_loop_are_only_optionally_visible_afterwards() -> None:
    """Given a Stage inside a loop, when a later Stage reads its Event, then only the optional form is accepted."""
    looped = workflow().each(tickets(), Flow("ticket").stage(make("close", NeedsTicket, Delivered)))

    with pytest.raises(DefinitionError, match="no earlier Stage definitely produces Delivered"):
        looped.stage(make("after", NeedsDelivered, Chored))
    assert looped.stage(make("after", NeedsOptionalDelivered, Chored)).check()


def test_a_reroute_outside_a_switch_is_rejected() -> None:
    """Given a reroute in no switch, when the Stage is added, then building fails."""
    with pytest.raises(DefinitionError, match="Stage 'fix': a reroute appears outside a dispatch case"):
        workflow().stage(make("fix").on_fail(reroute(Alpha)))


def test_a_reroute_to_a_sibling_case_is_accepted() -> None:
    """Given two cases, when one reroutes to the other, then the definition is accepted."""
    cases = {Alpha: Flow("a").stage(make("fix").on_fail(reroute(Beta))), Beta: Flow("b").stage(make("other"))}

    assert outcome_flow().dispatch(Outcome, cases).check()


def test_a_reroute_to_a_non_sibling_case_is_rejected() -> None:
    """Given a reroute to an Event that is no case of its switch, when it is added, then building fails."""
    cases = {Alpha: Flow("a").stage(make("fix").on_fail(reroute(Gamma))), Beta: stop.completed()}

    with pytest.raises(DefinitionError, match="Stage 'fix': reroute to Gamma, which is not a sibling case of Alpha"):
        outcome_flow().dispatch(Outcome, cases)


def test_reroutes_forming_a_cycle_are_rejected() -> None:
    """Given cases that reroute to each other, when the switch is added, then building fails naming the cycle."""
    cases = {
        Alpha: Flow("a").stage(make("from-alpha").on_fail(reroute(Beta))),
        Beta: Flow("b").stage(make("from-beta").on_fail(reroute(Alpha))),
    }

    with pytest.raises(DefinitionError, match=r"reroutes form a cycle Alpha -> Beta -> Alpha \(via Stage 'from-alpha', Stage 'from-beta'\)"):
        outcome_flow().dispatch(Outcome, cases)


def test_a_reroute_chain_that_ends_is_accepted() -> None:
    """Given three cases where reroutes only go one way, when the switch is added, then it is accepted."""
    decided = routed().stage(make("decide", produces=Triple))
    cases = {
        Alpha: Flow("a").stage(make("from-alpha").on_fail(reroute(Beta))),
        Beta: Flow("b").stage(make("from-beta").on_fail(reroute(Gamma))),
        Gamma: stop.completed(),
    }

    assert decided.dispatch(Triple, cases).check()


def test_a_field_reading_an_optional_event_after_a_conditional_stage_is_accepted_and_reported_optional() -> None:
    """Given a Stage that its condition may skip, when a later Stage reads it as optional, then it is accepted and optional."""
    skipped = routed().stage(make("chore", produces=Delivered).when(lambda _: False))

    definition = skipped.stage(make("after", NeedsOptionalDelivered, Chored))

    assert "after delivered event Delivered from chore (optional)" in wiring(definition)


def test_a_non_optional_read_after_a_conditional_stage_is_rejected_naming_the_condition() -> None:
    """Given a conditional producer, when a later Stage reads the Event as required, then the error names the producer."""
    skipped = routed().stage(make("chore", produces=Delivered).when(lambda _: False))

    with pytest.raises(DefinitionError, match="producer 'chore' may not, because its condition may skip it"):
        skipped.stage(make("after", NeedsDelivered, Chored))


def test_a_non_optional_read_after_a_producer_whose_failure_continues_is_rejected() -> None:
    """Given a producer whose fail route continues, when a later Stage reads the Event as required, then it is rejected."""
    soft = routed().stage(make("chore", produces=Delivered).on_fail(next_()))

    with pytest.raises(DefinitionError, match="its fail or hold route continues without it"):
        soft.stage(make("after", NeedsDelivered, Chored))


def test_the_services_a_definition_needs_come_from_its_inputs() -> None:
    """Given Stages reading a service, the context and an Event, when services are derived, then only service types are listed."""
    definition = routed().stage(make("deliver", NeedsClient, Delivered)).stage(make("spec", NeedsSpec, Chored))

    assert definition.services() == {Client}


def test_every_event_field_of_a_valid_definition_names_its_producer_in_the_wiring_table() -> None:
    """Given a valid definition, when explained, then each Event field row names its producer and the rest name their source."""
    definition = (
        routed()
        .stage(make("deliver", NeedsRouted, Delivered))
        .stage(make("spec", NeedsSpec, Chored))
        .stage(make("client", NeedsClient, Gamma))
    )

    assert definition.explain() == (
        "stage       field   source\n"
        "route-spec  -       no input\n"
        "deliver     routed  event Routed from route-spec\n"
        "spec        spec    context Spec\n"
        "client      client  service Client\n"
    )


def test_a_history_read_needs_a_producing_stage_but_only_when_the_definition_is_checked() -> None:
    """Given a History reader with no producer, when checked, then it is rejected; a later producer satisfies it."""
    reader = workflow().stage(make("learn", NeedsHistory, Delivered))

    with pytest.raises(DefinitionError, match="Stage 'learn' field 'past': no Stage of the definition produces Routed for its history"):
        reader.check()
    assert reader.stage(make("route-spec", produces=Routed)).check()


def test_an_all_read_needs_an_earlier_producer() -> None:
    """Given an All reader before any producer, when it is added, then building fails."""
    with pytest.raises(DefinitionError, match="Stage 'collect' field 'routed': no earlier Stage produces Routed"):
        workflow().stage(make("collect", NeedsAllRouted, Delivered))


def test_an_error_route_lists_specific_exception_types_once() -> None:
    """Given error routes, when a blanket, repeated, empty or non-exception type is listed, then building fails."""
    base = workflow()

    with pytest.raises(DefinitionError, match="cannot catch every exception"):
        base.on_error((Exception,), stop.failed())
    with pytest.raises(DefinitionError, match="needs at least one exception type"):
        base.on_error((), stop.failed())
    with pytest.raises(DefinitionError, match="is not an exception type"):
        base.on_error((int,), stop.failed())
    with pytest.raises(DefinitionError, match="ValueError already has an error route"):
        base.on_error((ValueError,), stop.failed()).on_error((ValueError, OSError), stop.failed())


def test_an_error_route_hand_off_is_checked_like_any_hand_off() -> None:
    """Given an error route to an agent Stage, when it is added, then building fails; an effect Stage is accepted."""
    with pytest.raises(DefinitionError, match="runs an agent"):
        workflow().on_error((OSError,), handoff(agent("comment")))
    assert workflow().on_error((OSError,), handoff(effect("comment", NeedsSpec))).check()


def test_a_flow_fragment_is_checked_without_its_surroundings_and_again_where_it_is_placed() -> None:
    """Given a fragment reading an upstream Event, when spliced after the producer, then it is accepted; elsewhere rejected."""
    fragment = Flow("fragment").stage(make("deliver", NeedsRouted, Delivered))

    assert routed().then(fragment).check()
    with pytest.raises(DefinitionError, match="no earlier Stage produces Routed"):
        workflow().then(fragment)


def test_a_workflow_cannot_be_used_as_a_fragment() -> None:
    """Given a whole Workflow, when it is spliced into another, then building fails."""
    with pytest.raises(DefinitionError, match="takes a Flow fragment"):
        workflow().then(workflow())


@dataclass(frozen=True)
class NeedsEveryPublished:
    published: All[Published]


def test_an_event_a_hand_off_produces_inside_a_loop_is_visible_to_the_stages_after_it() -> None:
    """Given a hand-off Stage that produces Published inside an each body, when a later Stage reads every Published, then it is accepted."""
    escalate = effect("escalate-ticket", NeedsTicket, Published)
    body = Flow("ticket").stage(agent("implement", NeedsTicket, Delivered).on_fail(rework(max_attempts=2, then=handoff(escalate))))

    definition = (
        routed().within(checkout(), Flow("deliver").each(tickets(), body, on_fail=next_()).stage(make("check", NeedsEveryPublished, Beta)))
    ).check()

    assert "check published all Published from escalate-ticket" in wiring(definition)

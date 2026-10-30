"""Definitions shaped like the two factory Workflows validate as a whole, so the analysis holds on realistic structure."""

from __future__ import annotations

from dataclasses import replace

import pytest

from loop import DefinitionError, Flow, Workflow, handoff, next_, next_item, reroute, rework, stop

from .support import (
    Alpha, Beta, Chored, Delivered, Discarded, Gamma, NeedsPublished, NeedsRouted, NeedsSpec, NeedsTicket, NoInput, Published,
    Routed, Spec, Triple, agent, checkout, effect, make, tickets,
)


def implement_spec_shaped() -> Workflow[Spec]:
    """Route, plan, deliver each ticket, publish three times, chore, finalize."""
    escalate = handoff(effect("escalate-ticket", NeedsTicket))
    to_human = handoff(effect("hand-to-human", NeedsSpec))
    ticket = (
        Flow("ticket")
        .stage(agent("implement-ticket", NeedsTicket, Delivered).on_fail(rework(max_attempts=2, then=escalate)))
        .stage(effect("close-ticket", NeedsTicket, Chored))
        .stage(effect("publish-draft", NeedsTicket, Published).named("publish-ticket"))
    )
    return (
        Workflow("implement_spec").context(Spec)
        .on_error((OSError,), handoff(effect("comment-failure", NeedsSpec)))
        .stage(make("route-spec", NeedsSpec, Routed).on_hold(to_human))
        .within(checkout(), Flow("plan").stage(agent("plan-spec", NeedsRouted, Gamma).on_hold(to_human)))
        .stage(effect("publish-draft", produces=Published).named("publish-earlier"))
        .within(
            checkout("feature-worktree"),
            Flow("deliver")
            .each(tickets(), ticket, on_fail=next_())
            .stage(effect("publish-draft", produces=Published).named("publish-delivery"))
            .stage(agent("refine", NoInput, Discarded).when(lambda _: True).on_fail(next_())),
        )
        .stage(make("finalize", NeedsPublished, Beta).reads(Published, from_="publish-delivery"))
    )


def test_a_definition_shaped_like_implement_spec_checks_and_reports_its_wiring() -> None:
    """Given three publish placements, when finalize names one, then the definition checks and wires finalize to it."""
    definition = implement_spec_shaped().check()

    rows = [" ".join(line.split()) for line in definition.explain().splitlines()]
    assert "finalize published event Published from publish-delivery" in rows
    assert "implement-ticket ticket item Ticket" in rows
    assert definition.services() == frozenset()


def test_finalize_must_name_its_producer_when_three_stages_publish() -> None:
    """Given three publish placements, when finalize does not name one, then the error lists every producer."""
    full = implement_spec_shaped()
    without_finalize = replace(full, nodes=full.nodes[:-1])

    with pytest.raises(DefinitionError, match="'publish-earlier', 'publish-ticket', 'publish-delivery'"):
        without_finalize.stage(make("finalize", NeedsPublished, Beta))


def babysit_shaped() -> Workflow[Spec]:
    """Poll each iteration, then address, maintain CI, or report; baseline red reroutes to CI."""
    block = handoff(effect("block-pr", NeedsSpec))
    address = Flow("address").within(
        checkout("pr-worktree"),
        Flow("batch")
        .stage(make("baseline", NeedsSpec, Chored).on_hold(reroute(Gamma)))
        .each(tickets("issues"), Flow("issue").stage(agent("resolve-issue", NeedsTicket, Delivered).on_hold(next_item()))),
    )
    ci = Flow("maintain-ci").within(checkout("ci-worktree"), Flow("ci").stage(agent("fix-ci", NeedsSpec, Discarded).on_fail(block)))
    return (
        Workflow("pr_babysit").context(Spec)
        .on_error((OSError,), block)
        .stage(effect("validate-origin", NeedsSpec, Routed).on_hold(block))
        .each(
            tickets("polls"),
            Flow("poll")
            .stage(make("decide-next", NeedsRouted, Triple))
            .dispatch(Triple, {Alpha: stop.completed(), Beta: address, Gamma: ci}),
        )
    )


def test_a_definition_shaped_like_pr_babysit_checks_with_a_reroute_through_a_workspace_scope() -> None:
    """Given a baseline hold that reroutes to the CI case from inside a workspace scope, when checked, then it is accepted."""
    assert babysit_shaped().check()


def test_the_wiring_is_stable_across_repeated_derivation() -> None:
    """Given a valid definition, when explained twice, then the text is identical."""
    definition = babysit_shaped()

    assert definition.explain() == definition.explain()

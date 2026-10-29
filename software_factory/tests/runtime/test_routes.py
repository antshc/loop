"""Routes: constructors, defaults and the terminal-route namespace."""

from __future__ import annotations

from software_factory.runtime import (
    Handoff, Next, NextItem, Reroute, Rework, Routes, Stop, handoff, next_, next_item, reroute, rework, stop,
)

from .support import Alpha, effect


def test_a_stage_routes_default_to_next_on_pass_and_stop_on_fail_and_hold() -> None:
    """Given no routes, when a Stage is defined, then pass goes on, fail stops failed and hold stops held."""
    assert Routes() == Routes(Next(), Stop("failed"), Stop("held"))


def test_the_route_constructors_build_the_route_values() -> None:
    """Given the route constructors, when called, then each builds its route value."""
    escalate = effect("escalate")

    assert next_() == Next()
    assert next_item() == NextItem()
    assert reroute(Alpha) == Reroute(Alpha)
    assert handoff(escalate) == Handoff(escalate)
    assert rework(max_attempts=2, then=handoff(escalate)) == Rework(2, Handoff(escalate))


def test_a_rework_without_a_then_stops_failed_after_the_bound() -> None:
    """Given a rework with no hand-off, when its bound is spent, then the scope stops failed."""
    assert rework(max_attempts=3).then == Stop("failed")


def test_the_terminal_routes_carry_their_status_and_reason() -> None:
    """Given the stop namespace, when each is called, then its status and optional reason are set."""
    assert (stop.completed(), stop.idle()) == (Stop("completed"), Stop("idle"))
    assert stop.held("waiting") == Stop("held", "waiting")
    assert stop.failed("broken") == Stop("failed", "broken")

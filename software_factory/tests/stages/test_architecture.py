"""Rules the import-linter cannot express: the shape of the Stage factories every Workflow definition calls."""

from __future__ import annotations

import inspect
import pkgutil
import typing
from collections.abc import Callable, Iterator

import software_factory.stages as stages
from software_factory.runtime import Selector, Stage, WorkspaceOpener
from software_factory.stages import chore, finalize, plan, publish, review, specs, tickets, workspaces

FACTORIES = (Stage, Selector, WorkspaceOpener)


def factories() -> Iterator[tuple[str, Callable[..., object]]]:
    """Every public function of the Stage modules that returns a Stage, a Selector or a workspace opener."""
    for module_info in pkgutil.iter_modules(stages.__path__):
        module = __import__(f"{stages.__name__}.{module_info.name}", fromlist=["_"])
        for name, function in inspect.getmembers(module, inspect.isfunction):
            returns = typing.get_type_hints(function).get("return")
            origin = typing.get_origin(returns) or returns
            if function.__module__ == module.__name__ and not name.startswith("_") and origin in FACTORIES:
                yield f"{module_info.name}.{name}", function


def test_no_stage_factory_takes_a_boolean_parameter() -> None:
    """Given every Stage factory in the library, when its signature is read, then no parameter is a bool: policy is values and callables."""
    found = list(factories())
    offenders = [
        f"{name}({parameter})"
        for name, function in found
        for parameter, hint in typing.get_type_hints(function).items()
        if parameter != "return" and hint is bool
    ]

    assert len(found) >= 20
    assert offenders == []


def test_the_stage_ids_are_unique_across_the_library_with_sample_parameters() -> None:
    """Given every Stage factory built with sample parameters, when their ids are collected, then no id repeats."""
    built = [
        specs.route_spec([]), specs.prepare_checkout(), specs.require_work(), specs.announce_delivered(),
        specs.hand_to_human(label="hitl"), specs.block_spec(label="hitl"), specs.comment_failure(),
        tickets.implement_ticket(), tickets.close_ticket(), tickets.escalate_ticket(label="hitl"), tickets.require_delivered(),
        publish.publish_draft_pr(), plan.plan_spec(), plan.create_tickets(), chore.refine(), review.review_pull_request(),
        finalize.finalize(label="hitl"),
    ]
    identities = [stage.id for stage in built] + [tickets.next_ready_ticket().id, workspaces.checkout().id, workspaces.feature_worktree().id]

    assert len(identities) == len(set(identities))

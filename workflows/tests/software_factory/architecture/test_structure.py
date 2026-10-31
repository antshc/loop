"""Rules the import-linter cannot express, all derived from the code: Workflow definitions, the Stage library, shared fragments, policy modules, the phase-reporting API and the ledger codec."""

from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import pkgutil
import typing
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import ModuleType

import pytest
from loop import (
    Declined,
    Event,
    Flow,
    Handoff,
    Message,
    Rework,
    Route,
    Selector,
    Stage,
    Workflow,
    WorkspaceOpener,
)
from loop.runtime._engine.codec import decode, encode
from loop.runtime.nodes import DispatchNode, EachNode, StageNode, WithinNode

import workflows.software_factory as factory
import workflows.software_factory.agents as agents
import workflows.software_factory.events as events
import workflows.software_factory.flows as flows
import workflows.software_factory.stages as stages
import workflows.software_factory.workflows as workflows
from workflows.software_factory.stages import pull_requests

from .samples import call_with_samples, sample_dataclass

FACTORIES = (Stage, Selector, WorkspaceOpener)

# The retired Phase-reporting names; `Workflow` is now the runtime's definition type.
PHASE_API = {"WorkflowBuilder", "WorkflowRun", "Phases", "Phase", "Kind"}

# What a policy module never imports: process execution, Git and GitHub clients and the private runtime engine.
CLIENTS = (
    "subprocess",
    "loop.runtime._engine",
    "loop.shared.process",
    "loop.shared.git",
    "workflows.software_factory.infrastructure.github.repo",
    "workflows.software_factory.infrastructure.github.account",
    "workflows.software_factory.infrastructure.github.issues.client",
    "workflows.software_factory.infrastructure.github.pull_requests.client",
)

type Part = Stage | Selector | WorkspaceOpener


def submodules(package: ModuleType) -> Iterator[ModuleType]:
    """Every module directly inside `package`, imported."""
    for info in pkgutil.iter_modules(package.__path__):
        yield importlib.import_module(f"{package.__name__}.{info.name}")


def workflow_packages() -> list[ModuleType]:
    """Every factory Workflow package: a factory package with an entry point."""
    root = Path(workflows.__file__).parent
    return [
        importlib.import_module(f"{workflows.__name__}.{info.name}")
        for info in pkgutil.iter_modules(workflows.__path__)
        if info.ispkg and (root / info.name / "__main__.py").exists()
    ]


def definition_factories() -> dict[str, Callable[..., Workflow]]:
    """The definition factory of every Workflow: the function its definition module names in `NAME`."""
    found = {}
    for package in workflow_packages():
        definition = importlib.import_module(f"{package.__name__}.definition")
        found[definition.NAME] = getattr(definition, definition.NAME)
    return found


def library_factories() -> dict[str, Callable[..., Part]]:
    """Every public function of the Stage modules that returns a Stage, a Selector or a workspace opener."""
    found = {}
    for module in submodules(stages):
        for name, function in inspect.getmembers(module, inspect.isfunction):
            returns = typing.get_type_hints(function).get("return")
            if (
                function.__module__ == module.__name__
                and not name.startswith("_")
                and (typing.get_origin(returns) or returns) in FACTORIES
            ):
                found[f"{module.__name__.rsplit('.', 1)[1]}.{name}"] = function
    return found


def fragment_factories() -> dict[str, Callable[..., Flow]]:
    """Every public function of the Flow fragment modules that returns a Flow."""
    found = {}
    for module in submodules(flows):
        for name, function in inspect.getmembers(module, inspect.isfunction):
            if (
                function.__module__ == module.__name__
                and not name.startswith("_")
                and typing.get_type_hints(function).get("return") is Flow
            ):
                found[f"{module.__name__.rsplit('.', 1)[1]}.{name}"] = function
    return found


def built_definitions() -> dict[str, Workflow]:
    """Every Workflow definition built with default settings."""
    return {
        name: call_with_samples(factory)
        for name, factory in definition_factories().items()
    }


def identity(part: Part) -> tuple[type, type, object]:
    """What a Stage, Selector or opener is, whatever id a Workflow places it under."""
    if isinstance(part, Stage):
        return Stage, part.input, part.produces
    if isinstance(part, Selector):
        return Selector, part.input, part.item
    return WorkspaceOpener, part.input, part.id


def route_parts(route: Route) -> Iterator[Part]:
    """The Stages a route runs: a hand-off's, and the one an exhausted rework hands off to."""
    if isinstance(route, Rework):
        route = route.then
    if isinstance(route, Handoff):
        yield from stage_parts(route.stage)


def stage_parts(stage: Stage) -> Iterator[Part]:
    """The Stage and every Stage its routes hand off to."""
    yield stage
    for route in (stage.routes.passed, stage.routes.failed, stage.routes.held):
        yield from route_parts(route)


def structure(flow: Flow) -> Iterator[Part]:
    """Every Stage, Selector and opener of the Flow in order, without what its routes hand off to."""
    for node in flow.nodes:
        match node:
            case StageNode(stage):
                yield stage
            case EachNode(select, body, _, _):
                yield select
                yield from structure(body)
            case DispatchNode(_, cases):
                for _, case in cases:
                    if isinstance(case, Flow):
                        yield from structure(case)
            case WithinNode(opener, body):
                yield opener
                yield from structure(body)


def placed(flow: Flow) -> Iterator[Part]:
    """Everything the Flow places: its structure and every Stage any route hands off to."""
    for node in flow.nodes:
        match node:
            case StageNode(stage):
                yield from stage_parts(stage)
            case EachNode(select, body, on_fail, on_hold):
                yield select
                yield from placed(body)
                yield from route_parts(on_fail)
                yield from route_parts(on_hold)
            case DispatchNode(_, cases):
                for _, case in cases:
                    yield from (
                        placed(case) if isinstance(case, Flow) else route_parts(case)
                    )
            case WithinNode(opener, body):
                yield opener
                yield from placed(body)
    if isinstance(flow, Workflow):
        for error_route in flow.error_routes:
            yield from route_parts(error_route.route)


def contains(sequence: list[object], run: list[object]) -> bool:
    """Whether `run` appears in `sequence` as one unbroken stretch."""
    return any(
        sequence[start : start + len(run)] == run
        for start in range(len(sequence) - len(run) + 1)
    )


def unused_stages(
    definitions: Mapping[str, Workflow], library: Mapping[str, Callable[..., Part]]
) -> list[str]:
    """The library factories whose Stage, Selector or opener no definition places."""
    in_use = {
        identity(part)
        for definition in definitions.values()
        for part in placed(definition)
    }
    return sorted(
        name
        for name, factory in library.items()
        if identity(call_with_samples(factory)) not in in_use
    )


def fragment_users(
    definitions: Mapping[str, Workflow], fragments: Mapping[str, Callable[..., Flow]]
) -> dict[str, list[str]]:
    """For each fragment, the definitions whose structure contains the fragment's Stages as one unbroken stretch."""
    structures = {
        name: [identity(part) for part in structure(definition)]
        for name, definition in definitions.items()
    }
    return {
        name: sorted(
            workflow
            for workflow, stretch in structures.items()
            if contains(
                stretch,
                [identity(part) for part in structure(call_with_samples(factory))],
            )
        )
        for name, factory in fragments.items()
    }


def boolean_parameters(library: Mapping[str, Callable[..., Part]]) -> list[str]:
    """Every `factory(parameter)` of the library whose parameter is a bool."""
    return [
        f"{name}({parameter})"
        for name, function in library.items()
        for parameter, hint in typing.get_type_hints(function).items()
        if parameter != "return" and hint is bool
    ]


@pytest.mark.parametrize("name", sorted(definition_factories()))
def test_every_workflow_definition_builds_with_default_settings_and_validates(
    name: str,
) -> None:
    """Given a Workflow definition factory built with default settings, when it is checked, then every route, Event and workspace requirement resolves."""
    call_with_samples(definition_factories()[name]).check()


def test_the_stage_ids_are_unique_across_the_library() -> None:
    """Given every Stage, Selector and opener factory built with sample parameters, when their ids are collected, then no id repeats."""
    built = {
        name: call_with_samples(factory)
        for name, factory in library_factories().items()
    }
    ids = [part.id for part in built.values()]

    assert len(built) >= 30
    assert sorted(id for id in ids if ids.count(id) > 1) == []


def test_every_library_stage_is_used_by_some_workflow() -> None:
    """Given every Workflow definition and every library Stage, Selector and opener, when usage is derived, then none is unused."""
    assert unused_stages(built_definitions(), library_factories()) == []


def test_a_library_stage_no_workflow_uses_is_reported_by_name() -> None:
    """Given definitions that leave out the Workflow that selects feedback, when usage is derived, then the feedback Stage is named as unused."""
    definitions = {
        name: definition
        for name, definition in built_definitions().items()
        if name != "address_once"
    }

    assert unused_stages(definitions, library_factories()) == ["feedback.select"]


def test_every_flow_fragment_is_used_by_two_workflows() -> None:
    """Given every Flow fragment and every Workflow definition, when usage is derived, then each fragment's Stages appear in at least two Workflows."""
    users = fragment_users(built_definitions(), fragment_factories())

    assert (
        users and {name: found for name, found in users.items() if len(found) < 2} == {}
    )


def test_a_flow_fragment_used_by_one_workflow_is_reported_by_name() -> None:
    """Given only one Workflow that places the address fragment, when usage is derived, then the fragment is named with its single user."""
    users = fragment_users(
        {"address_once": built_definitions()["address_once"]}, fragment_factories()
    )

    assert users == {"address.address_feedback": ["address_once"]}


def test_no_stage_factory_takes_a_boolean_parameter() -> None:
    """Given every Stage factory in the library, when its signature is read, then no parameter is a bool: policy is values and callables."""
    found = library_factories()

    assert len(found) >= 30
    assert boolean_parameters(found) == []


def switched_stage(*, strict: bool) -> Stage:
    """A Stage factory with a mode flag."""
    return pull_requests.validate_origin()


def test_a_stage_factory_with_a_boolean_parameter_is_reported_by_name() -> None:
    """Given a Stage factory with a bool parameter, when the signatures are read, then it is named with the parameter."""
    assert boolean_parameters({"requests.switched": switched_stage}) == [
        "requests.switched(strict)"
    ]


def policy_modules() -> list[Path]:
    """The policy module of every Workflow package."""
    return sorted(
        Path(package.__file__).parent / "policy.py" for package in workflow_packages()
    )


def is_dataclass_decorator(node: ast.expr) -> bool:
    """Whether a class decorator is `dataclass` or `dataclasses.dataclass`, called or not."""
    target = node.func if isinstance(node, ast.Call) else node
    return (isinstance(target, ast.Name) and target.id == "dataclass") or (
        isinstance(target, ast.Attribute) and target.attr == "dataclass"
    )


def imported_modules(tree: ast.AST) -> Iterator[str]:
    """Every module a tree imports, including each name imported from a package as a possible module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def policy_violations(owner: str, source: str) -> list[str]:
    """What a policy module's source breaks: a class with methods that is no dataclass, or an import of a client."""
    tree = ast.parse(source)
    behaviour = [
        f"{owner}: class {node.name} has methods"
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and not any(
            is_dataclass_decorator(decorator) for decorator in node.decorator_list
        )
        and any(
            isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
            for member in node.body
        )
    ]
    clients = [
        f"{owner}: imports {module}"
        for module in imported_modules(tree)
        if any(
            module == client or module.startswith(f"{client}.") for client in CLIENTS
        )
    ]
    return behaviour + clients


def test_policy_modules_hold_values_and_pure_functions_only() -> None:
    """Given every Workflow's policy module, when its source is read, then it defines no behaviour class and imports no client."""
    modules = policy_modules()

    assert len(modules) >= 3
    assert [
        violation
        for path in modules
        for violation in policy_violations(
            f"{path.parent.name}.policy", path.read_text()
        )
    ] == []


def test_a_policy_module_with_a_behaviour_class_or_a_client_import_is_reported_by_name() -> (
    None
):
    """Given a policy source with a class that has methods and imports of a client, when it is checked, then each is named."""
    source = (
        "import subprocess\n"
        "from workflows.software_factory.infrastructure.github.account import GitHub\n"
        "from dataclasses import dataclass\n\n"
        "class Fingerprinter:\n    def digest(self): ...\n\n"
        "@dataclass(frozen=True)\nclass Value:\n    def doubled(self): ...\n\n"
        "class Marker(RuntimeError): ...\n"
    )

    assert policy_violations("demo.policy", source) == [
        "demo.policy: class Fingerprinter has methods",
        "demo.policy: imports subprocess",
        "demo.policy: imports workflows.software_factory.infrastructure.github.account",
        "demo.policy: imports workflows.software_factory.infrastructure.github.account.GitHub",
    ]


def phase_api_uses(source: str) -> list[str]:
    """Where a module imports Loop's phase-reporting names or reaches them through the `loop` module."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[0] == "loop"
        ):
            found += [alias.name for alias in node.names if alias.name in PHASE_API]
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "loop"
            and node.attr in PHASE_API
        ):
            found.append(node.attr)
    return found


def test_the_factory_never_uses_the_loop_phase_api() -> None:
    """Given every module of the factory, when its imports from loop are scanned, then none names the phase-reporting API."""
    roots = [Path(factory.__file__).parent]
    modules = sorted(path for root in roots for path in root.rglob("*.py"))

    offenders = [
        f"{path}: {name}"
        for path in modules
        for name in phase_api_uses(path.read_text())
    ]

    assert len(modules) > 60
    assert offenders == []


def test_a_module_that_imports_a_phase_reporting_name_from_loop_is_reported() -> None:
    """Given sources that import phase names from loop, directly or through the module, when they are scanned, then each name is found; Loop's public Agent is not."""
    assert phase_api_uses("from loop import Agent, WorkflowBuilder, Phase") == [
        "WorkflowBuilder",
        "Phase",
    ]
    assert phase_api_uses("import loop\nloop.Phases") == ["Phases"]
    assert phase_api_uses("from loop import Workflow") == []


def defined_in(package: ModuleType, base: type) -> list[type]:
    """Every dataclass of the package's modules, at any depth, that subclasses `base`."""
    found: dict[str, type] = {}
    for info in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
        module = importlib.import_module(info.name)
        for _, member in inspect.getmembers(module, inspect.isclass):
            if (
                member.__module__ == module.__name__
                and issubclass(member, base)
                and member is not base
                and dataclasses.is_dataclass(member)
            ):
                found[f"{member.__module__}.{member.__qualname__}"] = member
    return [found[name] for name in sorted(found)]


def codec_subjects() -> list[type]:
    """Every Event and every agent message the ledger may hold."""
    # Declined is the agent message Loop's typed agent contract owns; every role may store it.
    return [*defined_in(events, Event), *defined_in(agents, Message), Declined]


@pytest.mark.parametrize(
    "subject",
    codec_subjects(),
    ids=lambda subject: (
        f"{subject.__module__.removeprefix('workflows.software_factory.')}.{subject.__qualname__}"
    ),
)
def test_every_event_and_agent_message_round_trips_through_the_ledger_codec(
    subject: type,
) -> None:
    """Given an Event or an agent message with sample values, when it is encoded and decoded as its own type, then it equals the original."""
    sample = sample_dataclass(subject)

    assert decode(encode(sample), subject) == sample


def test_the_codec_check_covers_every_event_and_agent_message() -> None:
    """Given the Event and agent message packages, when their classes are derived, then both kinds are covered."""
    subjects = codec_subjects()

    assert len([s for s in subjects if issubclass(s, Event)]) > 30
    assert len([s for s in subjects if issubclass(s, Message)]) > 10

"""Portability: each Workflow, copied with only its import closure, imports and builds its definition, and its closure holds no other Workflow."""

from __future__ import annotations

import ast
import importlib
import json
import os
import pkgutil
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import loop
import pytest

import software_factory
import factory_workflows as workflows

# Each factory package maps to the directory that holds it, mirroring the package-dir mapping in pyproject.toml.
PACKAGE_DIRS = {"software_factory": Path(software_factory.__file__).parent, "factory_workflows": Path(workflows.__file__).parent}

SAMPLES = Path(__file__).with_name("samples.py")

PROBE = """
import importlib, importlib.util, json, pathlib, sys
import portable_samples

root = pathlib.Path.cwd().resolve()
package = importlib.import_module(sys.argv[1])
if importlib.util.find_spec(sys.argv[1] + ".__main__"):
    importlib.import_module(sys.argv[1] + ".__main__")
definition = portable_samples.call_with_samples(getattr(package, package.NAME))
definition.check()
foreign = [
    name for name, module in sys.modules.items()
    if name.split(".")[0] in ("software_factory", "factory_workflows") and not str(getattr(module, "__file__", "")).startswith(str(root))
]
print(json.dumps({"name": definition.name, "foreign": foreign}))
"""


def workflow_names() -> list[str]:
    """The package name of every Workflow definition."""
    return sorted(info.name for info in pkgutil.iter_modules(workflows.__path__))


def is_type_checking(node: ast.expr) -> bool:
    """Whether an `if` test is `TYPE_CHECKING` or `typing.TYPE_CHECKING`."""
    return (isinstance(node, ast.Name) and node.id == "TYPE_CHECKING") or (isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING")


def runtime_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Every node of a tree except the imports a type checker alone needs."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.If) and is_type_checking(child.test):
            for orelse in child.orelse:
                yield from runtime_nodes(orelse)
        else:
            yield from runtime_nodes(child)


def module_file(name: str) -> Path | None:
    """The source file of a factory module, or None when `name` is no factory module."""
    top, *rest = name.split(".")
    if top not in PACKAGE_DIRS:
        return None
    base = PACKAGE_DIRS[top].joinpath(*rest)
    if (base / "__init__.py").exists():
        return base / "__init__.py"
    return base.with_suffix(".py") if base.with_suffix(".py").exists() else None


def absolute(node: ast.ImportFrom, package: str) -> str | None:
    """The absolute name of the module a `from` import reads, resolving its leading dots against `package`."""
    if not node.level:
        return node.module
    parts = package.split(".")
    return ".".join([*parts[: len(parts) - node.level + 1], *([node.module] if node.module else [])])


def imports_of(name: str, file: Path) -> Iterator[str]:
    """The factory modules a module imports at run time, each name imported from a package counting as a possible module."""
    package = name if file.name == "__init__.py" else name.rpartition(".")[0]
    for node in runtime_nodes(ast.parse(file.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = absolute(node, package)
            yield base
            yield from (f"{base}.{alias.name}" for alias in node.names)


def closure_of(package: str) -> dict[str, Path]:
    """Every factory module a Workflow package needs: its own modules, what they import and the packages those live in."""
    pending = [f"{package}.{info.name}" for info in pkgutil.iter_modules(importlib.import_module(package).__path__)] + [package]
    found: dict[str, Path] = {}
    while pending:
        name = pending.pop()
        file = module_file(name)
        if file is None or name in found:
            continue
        found[name] = file
        parents = [".".join(name.split(".")[:size]) for size in range(1, len(name.split(".")))]
        pending += [*parents, *imports_of(name, file)]
    return found


def copy_closure(closure: dict[str, Path], target: Path) -> None:
    """Copies each module of the closure and the data files beside it into `target`, keeping their paths."""
    for name, file in closure.items():
        parts = name.split(".")
        destination = target.joinpath(*parts, "__init__.py") if file.name == "__init__.py" else target.joinpath(*parts[:-1], f"{parts[-1]}.py")
        destination.parent.mkdir(parents=True, exist_ok=True)
        for sibling in file.parent.iterdir():
            if sibling.is_file() and (sibling == file or sibling.suffix not in (".py", ".pyc")):
                shutil.copy2(sibling, destination.parent / sibling.name)
    shutil.copy2(SAMPLES, target / "portable_samples.py")


def probe(package: str, target: Path) -> subprocess.CompletedProcess[str]:
    """Imports the Workflow package and its entry point in a fresh interpreter that sees only `target` and Loop."""
    loop_source = target.with_name(f"{target.name}-loop")
    loop_source.mkdir(exist_ok=True)
    if not (loop_source / "loop").exists():
        (loop_source / "loop").symlink_to(Path(loop.__file__).parent)
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join([str(target), str(loop_source)])}
    return subprocess.run([sys.executable, "-c", PROBE, package], cwd=target, env=environment, capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("name", workflow_names())
def test_each_workflow_runs_from_its_import_closure_alone(name: str, tmp_path: Path) -> None:
    """Given a Workflow copied with only its import closure, when its package and entry point are imported and its definition built, then it validates and imports nothing outside the copy."""
    package = f"{workflows.__name__}.{name}"
    closure = closure_of(package)
    copy_closure(closure, tmp_path)

    result = probe(package, tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["foreign"] == []


@pytest.mark.parametrize("name", workflow_names())
def test_a_workflows_import_closure_holds_no_other_workflow_and_no_tests(name: str) -> None:
    """Given the import closure of a Workflow, when its modules are listed, then no other Workflow and no test module is among them."""
    package = f"{workflows.__name__}.{name}"

    unexpected = sorted(
        module for module in closure_of(package)
        if module.startswith(f"{workflows.__name__}.") and not module.startswith(package) and module != workflows.__name__
        or module.split(".")[0] == "tests"
    )

    assert unexpected == []


def test_a_workflow_that_needs_another_workflow_does_not_run_from_its_own_closure(tmp_path: Path) -> None:
    """Given a closure missing a module the Workflow imports, when its package is imported in the copy, then the import fails."""
    package = f"{workflows.__name__}.address_once"
    closure = {name: file for name, file in closure_of(package).items() if name != "software_factory.flows.address"}
    copy_closure(closure, tmp_path)

    result = probe(package, tmp_path)

    assert result.returncode != 0 and "software_factory.flows" in result.stderr

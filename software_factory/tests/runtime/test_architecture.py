"""The factory runtime reports through its own spans and never reaches for Loop's phase-reporting API."""

from __future__ import annotations

import ast
from pathlib import Path

import software_factory.runtime as runtime

PHASE_API = {"Workflow", "WorkflowBuilder", "WorkflowRun", "Phases", "Phase", "Kind"}


def test_the_runtime_never_imports_loops_phase_reporting_api() -> None:
    """Given every runtime module, when its imports from loop are scanned, then none names the phase-reporting API."""
    root = Path(runtime.__file__).parent
    offenders = [
        f"{path.relative_to(root)}: {alias.name}"
        for path in root.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "loop"
        for alias in node.names
        if alias.name in PHASE_API
    ]

    assert offenders == []

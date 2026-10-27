"""Disposable: mirror the real loop/workflows import graph as an imports-only TS skeleton.

Every Python module becomes a .ts file (packages -> index.ts) holding one side-effect
import per first-party import found anywhere in the module (incl. lazy and TYPE_CHECKING
imports, as grimp counts them). `opentelemetry*` imports become `@opentelemetry/api`.
"""

from __future__ import annotations

import ast
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "skeleton"
ROOTS = {"loop": REPO / "src" / "loop", "workflows": REPO / "workflows"}


def discover() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for root, base in ROOTS.items():
        for path in base.rglob("*.py"):
            rel = path.relative_to(base)
            if "tests" in rel.parts:
                continue
            parts = [root, *rel.with_suffix("").parts]
            if parts[-1] == "__init__":
                parts = parts[:-1]
            modules[".".join(parts)] = path
    return modules


def ts_path(module: str, modules: dict[str, Path]) -> Path:
    root, *rest = module.split(".")
    base = Path("src/loop") if root == "loop" else Path("workflows")
    if modules[module].name == "__init__.py":
        return base.joinpath(*rest, "index.ts")
    return base.joinpath(*rest[:-1], rest[-1] + ".ts")


def most_specific(name: str, modules: dict[str, Path]) -> str | None:
    parts = name.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in modules:
            return candidate
        parts.pop()
    return None


def targets(module: str, path: Path, modules: dict[str, Path]) -> set[str]:
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join(anchor + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            names = [f"{base}.{alias.name}" for alias in node.names] + [base]
            # `from X import name` targets X.name when it is a module, else X.
            names = [n for n in names[:-1] if n in modules] or [base]
        for name in names:
            if name.split(".")[0] == "opentelemetry":
                found.add("@opentelemetry/api")
            elif (target := most_specific(name, modules)) and target != module:
                found.add(target)
    return found


def main() -> None:
    modules = discover()
    shutil.rmtree(OUT, ignore_errors=True)
    edges = 0
    for module, path in sorted(modules.items()):
        dest = OUT / ts_path(module, modules)
        dest.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"// mirrors {module}"]
        for target in sorted(targets(module, path, modules)):
            if target.startswith("@"):
                spec = target
            else:
                rel = os.path.relpath(OUT / ts_path(target, modules), dest.parent)
                spec = rel if rel.startswith(".") else f"./{rel}"
            lines.append(f'import "{spec}";')
            edges += 1
        dest.write_text("\n".join(lines) + "\n")
    print(f"skeleton: {len(modules)} modules, {edges} imports", file=sys.stderr)


if __name__ == "__main__":
    main()

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

from ship_proto.runtime.contracts.command import Command


def discover_commands(
    root: Path,
    package: str | None = None,
    into: dict[str, Command] | None = None,
) -> dict[str, Command]:
    # Loading a workflow executes its code; only point this at trusted directories.
    # `package` is the importable dotted name of `root`; without it, root's parent joins sys.path.
    root = root.resolve()
    if package is None:
        if str(root.parent) not in sys.path:
            sys.path.insert(0, str(root.parent))
        package = root.name
    commands = {} if into is None else into
    for entry in sorted(root.iterdir()):
        if not (entry / "command.py").is_file():
            continue
        command = import_module(f"{package}.{entry.name}.command").command
        if command.name in commands:
            raise ValueError(f"duplicate command: {command.name}")
        commands[command.name] = command
    return commands

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

from afk_proto.runtime.contracts.command import Command


def discover_commands(root: Path) -> dict[str, Command]:
    # Loading a slice executes its code; only point this at trusted directories.
    root = root.resolve()
    if str(root.parent) not in sys.path:
        sys.path.insert(0, str(root.parent))
    commands: dict[str, Command] = {}
    for entry in sorted(root.iterdir()):
        if not (entry / "command.py").is_file():
            continue
        command = import_module(f"{root.name}.{entry.name}.command").command
        if command.name in commands:
            raise ValueError(f"duplicate command: {command.name}")
        commands[command.name] = command
    return commands

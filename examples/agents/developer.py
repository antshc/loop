from __future__ import annotations

from pathlib import Path

from loop import AgentProfile, Instructions, Permissions, copilot

ROOT = Path(__file__).resolve().parents[2]

developer = AgentProfile(
    cli=copilot,
    name="developer",
    # Leave model unset to use the installed CLI's default.
    reasoning_effort="high",
    instructions=Instructions.from_file(Path(__file__).with_name("developer.md")),
    cwd=ROOT,
    add_dirs=(ROOT / "docs",),
    permissions=Permissions(tools="allow-all", deny_tools=("shell(git push)",)),
    env={"DOTNET_ENVIRONMENT": "Development", "LOG_LEVEL": "debug"},
    timeout_sec=1800,
    args=(),
)

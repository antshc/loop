from __future__ import annotations

from pathlib import Path

from loop import AgentProfile, Instructions, Permissions, copilot

_ROOT = Path(__file__).resolve().parent
# Native denials reinforce prompts; agents still run with host-user permissions.
_PERMISSIONS = Permissions(deny_tools=("shell(git commit*)", "shell(git push*)", "shell(gh*)"))
ADDRESS = AgentProfile(copilot, name="address", permissions=_PERMISSIONS,
                       instructions=Instructions.from_file(_ROOT / "address.md"), timeout_sec=1800)
MAINTAINER = AgentProfile(copilot, name="maintainer", permissions=_PERMISSIONS,
                          instructions=Instructions.from_file(_ROOT / "maintainer.md"), timeout_sec=1800)

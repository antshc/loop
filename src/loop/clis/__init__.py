from __future__ import annotations

from .base import AgentCli, AgentProfile
from .codex_cli import CodexCli
from .copilot_cli import CopilotCli
from .runner import CliRunner, ProcessCliRunner

copilot = CopilotCli()
codex = CodexCli()
DEFAULT = AgentProfile(copilot)

__all__ = [
    "AgentCli",
    "AgentProfile",
    "CliRunner",
    "CodexCli",
    "CopilotCli",
    "DEFAULT",
    "ProcessCliRunner",
    "codex",
    "copilot",
]

from __future__ import annotations

from .base import AgentCli, AgentProfile
from .codex_cli import CodexCli
from .copilot_cli import CopilotCli
from .copilot_log import CopilotLogReader
from .observing import ObservingCliRunner, SessionLogReader
from .runner import CliRunner, ProcessCliRunner
from .transport import CodexJson, CopilotJson, CopilotText, Transcript, Transport

copilot = CopilotCli()
codex = CodexCli()
DEFAULT = AgentProfile(copilot)

__all__ = [
    "AgentCli",
    "AgentProfile",
    "CliRunner",
    "CodexCli",
    "CodexJson",
    "CopilotCli",
    "CopilotJson",
    "CopilotLogReader",
    "CopilotText",
    "DEFAULT",
    "ObservingCliRunner",
    "ProcessCliRunner",
    "SessionLogReader",
    "Transcript",
    "Transport",
    "codex",
    "copilot",
]

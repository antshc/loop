"""A scripted agent CLI runner for the typed role agents, driven through Loop's fake agent builder."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import Any

from loop import AgentProfile, AgentRequest, fake_agent

from software_factory.agents import TypedAgent


@dataclass
class ScriptedRunner:
    """Answers every run with `output` and `exit_code`, or raises `error`; records what it was asked."""

    output: str = ""
    exit_code: int = 0
    error: BaseException | None = None
    profiles: list[AgentProfile] = field(default_factory=list)
    requests: list[AgentRequest] = field(default_factory=list)

    def run(self, profile, request, turn, context):
        self.profiles.append(profile)
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return profile.cli.parse(self.output, turn, self.exit_code)


def run_agent(agent: TypedAgent, request: Any, runner: ScriptedRunner, *, session: str | None = None) -> Any:
    """Runs `agent` once against `runner` in a fake workspace."""
    with fake_agent(runner).open() as workspace:
        return agent.run(workspace, request, session=session)


def envelope(status: str = "completed", identifier: str | None = None, **result: Any) -> str:
    """Agent output ending in a status envelope."""
    body: dict[str, Any] = {"status": status, "result": result}
    if identifier is not None:
        body["identifier"] = identifier
    return f"thinking...\n{json.dumps(body)}\nbye"


def process_failure() -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(1, ["copilot"], stderr="boom")


def timeout() -> subprocess.TimeoutExpired:
    return subprocess.TimeoutExpired(["copilot"], 5)

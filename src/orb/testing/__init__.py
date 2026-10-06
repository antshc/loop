"""Test doubles for every orb process boundary: gh, git, docker, the Copilot CLI, and the agent client."""

from __future__ import annotations

from orb.agents.fake_agent_client import FakeAgentClient
from orb.agents.fake_copilot_cli import FakeCopilotCli
from orb.capsules.fake_docker import FakeDocker
from orb.platforms.fake_gh_cli import FakeGhCli
from orb.platforms.fake_git_client import FakeGitClient

__all__ = [
    "FakeAgentClient",
    "FakeCopilotCli",
    "FakeDocker",
    "FakeGhCli",
    "FakeGitClient",
]

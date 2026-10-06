"""Test doubles for every loop process boundary: gh, git, docker, the Copilot CLI, and the agent client."""

from __future__ import annotations

from loop.agents.fake_agent_client import FakeAgentClient
from loop.agents.fake_copilot_cli import FakeCopilotCli
from loop.sandboxes.fake_docker import FakeDocker
from loop.platforms.fake_gh_cli import FakeGhCli
from loop.platforms.fake_git_client import FakeGitClient

__all__ = [
    "FakeAgentClient",
    "FakeCopilotCli",
    "FakeDocker",
    "FakeGhCli",
    "FakeGitClient",
]

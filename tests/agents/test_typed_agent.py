"""The typed agent contract: a workspace and a typed request in, a typed reply or a typed error out."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import pytest

from loop import (
    AgentProfile, AgentReplyError, AgentRequest, AgentResult, AgentRunError, Message, ReplyMismatch, TypedAgent, copilot,
)


@dataclass(frozen=True)
class Greeting(Message):
    name: str


class FakeClient:
    def __init__(self, result: AgentResult | Exception) -> None:
        self.result = result
        self.requests: list[AgentRequest] = []

    def run(self, request: AgentRequest) -> AgentResult:
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeWorkspace:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.profiles: list[AgentProfile] = []

    def agent(self, profile: AgentProfile) -> FakeClient:
        self.profiles.append(profile)
        return self.client


def decode(_: Greeting, output: str) -> str:
    if not output.startswith("hello"):
        raise ReplyMismatch("no greeting")
    return output


GREETER: TypedAgent[Greeting, str] = TypedAgent(
    "greeter", AgentProfile(copilot), lambda request: f"greet {request.name}", decode,
)


def test_a_matching_output_is_decoded_into_the_typed_reply() -> None:
    """Given output the decoder accepts, when the agent runs, then the reply is the decoded value."""
    client = FakeClient(AgentResult("hello Ada", "s1", 0))

    assert GREETER.run(FakeWorkspace(client), Greeting("Ada"), session="s1") == "hello Ada"
    assert client.requests[0].prompt == "greet Ada"


def test_the_encoded_prompt_and_the_session_name_reach_the_agent_request() -> None:
    """Given a session name, when the agent runs, then the request carries the encoded prompt and that name."""
    client = FakeClient(AgentResult("hello Ada", "s1", 0))
    workspace = FakeWorkspace(client)

    GREETER.run(workspace, Greeting("Ada"), session="s1")

    assert client.requests[0].prompt == "greet Ada"
    assert client.requests[0].name == "s1"
    assert workspace.profiles == [GREETER.profile]


def test_a_reply_mismatch_is_reported_as_an_agent_reply_error() -> None:
    """Given output the decoder rejects, when the agent runs, then an AgentReplyError names the role."""
    with pytest.raises(AgentReplyError, match="greeter agent: no greeting"):
        GREETER.run(FakeWorkspace(FakeClient(AgentResult("bye", "s1", 0))), Greeting("Ada"))


def test_a_non_zero_exit_code_is_reported_as_an_agent_run_error() -> None:
    """Given an agent that exits non-zero, when it runs, then an AgentRunError carries the exit code."""
    with pytest.raises(AgentRunError, match="exited with code 2"):
        GREETER.run(FakeWorkspace(FakeClient(AgentResult("hello", "s1", 2))), Greeting("Ada"))


def test_a_failing_process_is_reported_as_an_agent_run_error() -> None:
    """Given a process failure, when the agent runs, then an AgentRunError wraps it."""
    failure = subprocess.CalledProcessError(1, ["copilot"])

    with pytest.raises(AgentRunError, match="process failed"):
        GREETER.run(FakeWorkspace(FakeClient(failure)), Greeting("Ada"))


def test_tuning_replaces_profile_fields_without_changing_the_original() -> None:
    """Given a typed agent, when it is tuned, then only the copy carries the new profile field."""
    tuned = GREETER.tuned(timeout_sec=5.0)

    assert tuned.profile.timeout_sec == 5.0
    assert GREETER.profile.timeout_sec is None

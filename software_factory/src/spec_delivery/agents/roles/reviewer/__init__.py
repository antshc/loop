"""The reviewer role: reviews a pull request read-only and posts its own findings."""

from __future__ import annotations

from dataclasses import replace

from loop import DEFAULT, Declined, Role, TypedAgent
from loop.software_factory import agents as agent_tools

from .messages import (
    Reviewed,
    ReviewedAxis,
    ReviewedFinding,
    ReviewerReply,
    ReviewerRequest,
)


def _decode(request: ReviewerRequest, output: str) -> ReviewerReply:
    envelope = agent_tools.read_status(output)
    if envelope.failed:
        return envelope.declined()
    result = envelope.result
    axes = tuple(
        ReviewedAxis(
            agent_tools.text(item, "name", required=True),
            agent_tools.text(item, "outcome", required=True).lower(),
            agent_tools.text(item, "note"),
        )
        for item in agent_tools.objects(result, "axes", "axis")
    )
    findings = tuple(
        ReviewedFinding(
            agent_tools.text(item, "kind", required=True).lower(),
            agent_tools.text(item, "summary", required=True),
            agent_tools.text(item, "axis"),
        )
        for item in agent_tools.objects(result, "findings", "finding")
    )
    return Reviewed(
        axes,
        findings,
        tuple(str(item) for item in envelope.items("posted")),
        envelope.text("head"),
    )


REVIEWER: TypedAgent[ReviewerRequest, ReviewerReply] = agent_tools.template_agent(
    "reviewer",
    package=__name__,
    profile=replace(DEFAULT, name=Role.REVIEWER),
    args={
        "SOURCE_ROOT": lambda request: str(request.source_root),
        "PULL_REQUEST_URL": lambda request: request.pull_request_url,
        "INITIATIVE": lambda request: request.initiative,
        "HEAD_SHA": lambda request: request.head_sha,
    },
    decode=_decode,
)

__all__ = [
    "REVIEWER",
    "Declined",
    "Reviewed",
    "ReviewedAxis",
    "ReviewedFinding",
    "ReviewerReply",
    "ReviewerRequest",
]

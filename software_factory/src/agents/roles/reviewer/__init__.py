"""The reviewer role: reviews a pull request read-only and posts its own findings."""

from __future__ import annotations

from dataclasses import replace

from loop import DEFAULT, Role

from ... import _kit
from ...contract import Declined, TypedAgent
from .messages import Reviewed, ReviewedAxis, ReviewedFinding, ReviewerReply, ReviewerRequest


def _decode(request: ReviewerRequest, output: str) -> ReviewerReply:
    envelope = _kit.read_status(output)
    if envelope.failed:
        return envelope.declined()
    result = envelope.result
    axes = tuple(
        ReviewedAxis(_kit.text(item, "name", required=True), _kit.text(item, "outcome", required=True).lower(), _kit.text(item, "note"))
        for item in _kit.objects(result, "axes", "axis")
    )
    findings = tuple(
        ReviewedFinding(_kit.text(item, "kind", required=True).lower(), _kit.text(item, "summary", required=True), _kit.text(item, "axis"))
        for item in _kit.objects(result, "findings", "finding")
    )
    return Reviewed(axes, findings, tuple(str(item) for item in envelope.items("posted")), envelope.text("head"))


REVIEWER: TypedAgent[ReviewerRequest, ReviewerReply] = _kit.template_agent(
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

__all__ = ["REVIEWER", "Declined", "Reviewed", "ReviewedAxis", "ReviewedFinding", "ReviewerReply", "ReviewerRequest"]

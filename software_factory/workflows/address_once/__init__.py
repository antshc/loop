"""address_once as one declarative definition over existing Stages and the shared address fragment: address one pull request's trusted feedback, once."""

from __future__ import annotations

from software_factory.events.pull_requests import PullRequestWork
from software_factory.flows import address
from software_factory.runtime import Verify, Workflow
from software_factory.stages import feedback, pull_requests

from .policy import TRUST, address_subject

NAME = "address_once"

__all__ = ["NAME", "address_once"]


def address_once(verify: Verify) -> Workflow[PullRequestWork]:
    """One pull request: validate its origin, snapshot it, select the trusted feedback, then address, publish and reply once; no feedback holds."""
    return (
        Workflow(NAME).context(PullRequestWork)
        .stage(pull_requests.validate_origin())
        .stage(pull_requests.snapshot())
        .stage(feedback.select(trust=TRUST))
        .then(address.address_feedback(message=address_subject, verify=verify))
    )

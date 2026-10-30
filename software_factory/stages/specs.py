"""Spec Stages: route a Spec, prepare its checkout, require work, announce delivery and hand a Spec to a person."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from software_factory.events.pull_requests import DraftPullRequestPublished
from software_factory.events.specs import (
    CheckoutPrepared, DeliveryAnnounced, RepositoryTarget, SpecBlocked, SpecFailureCommented, SpecHandedToHuman, SpecHeld,
    SpecRouted, SpecWork, WorkFound,
)
from software_factory.runtime import Evidence, Fail, Hold, History, Pass, Stage, Verdict
from software_factory.shared.git import GitClient
from software_factory.shared.github.issues.client import Issues
from software_factory.shared.github.issues.graph import SpecGraphSnapshot

_VERSION = re.compile(r"(\d+(?:\.\d+)+)")


@dataclass(frozen=True)
class SpecInput:
    work: SpecWork
    tracker: Issues


@dataclass(frozen=True)
class PrepareInput:
    routed: SpecRouted
    git: GitClient


@dataclass(frozen=True)
class AnnounceInput:
    work: SpecWork
    tracker: Issues
    published: DraftPullRequestPublished


@dataclass(frozen=True)
class HandoffInput:
    work: SpecWork
    tracker: Issues
    held: History[SpecHeld]


@dataclass(frozen=True)
class FailureInput:
    work: SpecWork
    tracker: Issues


def feature_branch(spec: SpecGraphSnapshot) -> str:
    """`<version_with_underscores>_<title slug>` when the base branch carries a version, else `<title slug>`."""
    slug = re.sub(r"[^a-z0-9]+", "-", spec.bare_title.lower()).strip("-") or "spec"
    match = _VERSION.search(spec.base_branch or "")
    return slug if match is None else f"{match[1].replace('.', '_')}_{slug}"


def route_spec(targets: Sequence[RepositoryTarget]) -> Stage[SpecInput, SpecGraphSnapshot, SpecRouted]:
    """Routes the Spec to the configured repository its labels name; a Spec that cannot be routed is held before anything is fetched."""
    by_slug = _by_slug(targets)
    return (
        Stage("route-spec", SpecInput, produces=SpecRouted, may_emit=(SpecHeld,))
        .action(lambda i: i.tracker.spec(i.work.number))
        .gate(lambda _, spec, __: _routed(spec, by_slug))
    )


def prepare_checkout() -> Stage[PrepareInput, bool, CheckoutPrepared]:
    """Fetches the target checkout; holds when the Spec's base branch does not exist on origin."""
    return (
        Stage("prepare-checkout", PrepareInput, produces=CheckoutPrepared, may_emit=(SpecHeld,))
        .action(_fetch)
        .gate(_base_exists)
    )


def require_work() -> Stage[SpecInput, SpecGraphSnapshot, WorkFound]:
    """Passes when the Spec has an actionable, READY Ticket; holds, with nothing to hand off, when it has none."""
    return (
        Stage("require-work", SpecInput, produces=WorkFound)
        .action(lambda i: i.tracker.spec(i.work.number))
        .gate(lambda _, spec, __: _work(spec))
    )


def announce_delivered(*, source: str = "implement_spec") -> Stage[AnnounceInput, DeliveryAnnounced, DeliveryAnnounced]:
    """Tells the Spec its Tickets are delivered and where the draft pull request is; `source` prefixes the comment."""
    return Stage("announce-delivered", AnnounceInput, produces=DeliveryAnnounced).effect(
        _reconciled(_announce(source), _announced(source)), reconcile=_announced(source),
    )


def hand_to_human(*, label: str, source: str = "implement_spec") -> Stage[HandoffInput, SpecHandedToHuman, SpecHandedToHuman]:
    """Comments why a person is needed, then labels the Spec `label`; the label is written last so it marks a finished hand-off."""
    return Stage("hand-to-human", HandoffInput, produces=SpecHandedToHuman).effect(
        _reconciled(_hand_over(label, source), _handed_over(label, source)), reconcile=_handed_over(label, source),
    )


def block_spec(*, label: str, source: str = "implement_spec") -> Stage[HandoffInput, SpecBlocked, SpecBlocked]:
    """Hands the Spec to a person like `hand_to_human`, but only when it has work the block holds back."""
    return Stage("block-spec", HandoffInput, produces=SpecBlocked).effect(
        _reconciled(_block(label, source), _blocked(label, source)), reconcile=_blocked(label, source),
    )


def comment_failure(*, source: str = "implement_spec") -> Stage[FailureInput, SpecFailureCommented, SpecFailureCommented]:
    """Comments on the Spec that its run failed; the details stay in the run's ledger and spans."""
    return Stage("comment-failure", FailureInput, produces=SpecFailureCommented).effect(
        _reconciled(_comment_failure(source), _failure_commented(source)), reconcile=_failure_commented(source),
    )


def _by_slug(targets: Sequence[RepositoryTarget]) -> dict[str, RepositoryTarget]:
    """The targets by case-folded slug; a slug configured twice is a definition mistake."""
    configured: dict[str, RepositoryTarget] = {}
    for target in targets:
        folded = target.slug.casefold()
        if folded in configured:
            raise ValueError(f"duplicate repository target: {folded}")
        configured[folded] = target
    return configured


def _routed(spec: SpecGraphSnapshot, targets: dict[str, RepositoryTarget]) -> Verdict[SpecRouted]:
    if not spec.complete:
        return Fail(f"Spec #{spec.number} was not read completely; no decision is made from it")
    if spec.target is None or spec.base_branch is None:
        return _held(f"cannot resolve the repo:target and repo:base labels on {spec.url}")
    target = targets.get(spec.target.casefold())
    if target is None:
        return _held(f"repo:target:{spec.target} is not a configured repository")
    return Pass(SpecRouted(target, spec.initiative, spec.base_branch, feature_branch(spec)))


def _held(reason: str) -> Hold[SpecHeld]:
    return Hold(reason, SpecHeld(reason))


def _fetch(i: PrepareInput) -> bool:
    """Fetches and says whether the base branch exists on origin."""
    i.git.fetch(i.routed.target.checkout)
    return i.git.remote_branch_exists(i.routed.target.checkout, i.routed.base_branch)


def _base_exists(i: PrepareInput, exists: bool, _: Evidence) -> Verdict[CheckoutPrepared]:
    if exists:
        return Pass(CheckoutPrepared(i.routed.base_branch))
    return _held(f"target branch {i.routed.base_branch!r} does not exist on {i.routed.target.slug}")


def _work(spec: SpecGraphSnapshot) -> Verdict[WorkFound]:
    ready = tuple(ticket.number for ticket in spec.deliverable_tickets if ticket.actionable)
    return Pass(WorkFound(ready)) if ready else Hold("no Ticket is actionable")


def _reconciled(run: Any, reconcile: Any) -> Any:
    """Runs only what a crashed attempt did not already do; the runtime does not reconcile effects itself yet."""
    return lambda i: reconcile(i) or run(i)


def _message(source: str, text: str) -> str:
    return f"{source}: {text}"


def _announce(source: str) -> Any:
    def run(i: AnnounceInput) -> DeliveryAnnounced:
        url = i.published.url
        if url is not None:
            i.tracker.comment(i.work.number, "delivered", _delivered_message(source, url))
        return DeliveryAnnounced(url)
    return run


def _announced(source: str) -> Any:
    def reconcile(i: AnnounceInput) -> DeliveryAnnounced | None:
        url = i.published.url
        if url is None:
            return DeliveryAnnounced(None)
        return DeliveryAnnounced(url) if _commented(i.tracker, i.work.number, _delivered_message(source, url)) else None
    return reconcile


def _delivered_message(source: str, url: str) -> str:
    return _message(source, f"all Tickets delivered; draft pull request: {url}")


def _reason(held: Sequence[SpecHeld]) -> str:
    return held[-1].reason if held else "a person needs to look at this Spec"


def _hand_over(label: str, source: str) -> Any:
    def run(i: HandoffInput) -> SpecHandedToHuman:
        message = _message(source, _reason(i.held))
        i.tracker.comment(i.work.number, "hand-off", message)
        i.tracker.add_label(i.work.number, label)
        return SpecHandedToHuman(label, message)
    return run


def _handed_over(label: str, source: str) -> Any:
    def reconcile(i: HandoffInput) -> SpecHandedToHuman | None:
        if label not in i.tracker.issue(i.work.number).labels:
            return None
        return SpecHandedToHuman(label, _message(source, _reason(i.held)))
    return reconcile


def _block(label: str, source: str) -> Any:
    def run(i: HandoffInput) -> SpecBlocked:
        message = _message(source, _reason(i.held))
        if not i.tracker.spec(i.work.number).has_work:
            return SpecBlocked(label, message, labelled=False)
        _hand_over(label, source)(i)
        return SpecBlocked(label, message, labelled=True)
    return run


def _blocked(label: str, source: str) -> Any:
    def reconcile(i: HandoffInput) -> SpecBlocked | None:
        if label not in i.tracker.issue(i.work.number).labels:
            return None
        return SpecBlocked(label, _message(source, _reason(i.held)), labelled=True)
    return reconcile


def _comment_failure(source: str) -> Any:
    def run(i: FailureInput) -> SpecFailureCommented:
        message = _failure_message(source)
        i.tracker.comment(i.work.number, "failure", message)
        return SpecFailureCommented(message)
    return run


def _failure_commented(source: str) -> Any:
    def reconcile(i: FailureInput) -> SpecFailureCommented | None:
        message = _failure_message(source)
        return SpecFailureCommented(message) if _commented(i.tracker, i.work.number, message) else None
    return reconcile


def post_failure_comment(tracker: Issues, number: int, *, source: str = "implement_spec") -> None:
    """Comments on Spec `number` that its run failed, as `comment_failure` does, unless the Comment is already there; for an error that escaped the run."""
    message = _failure_message(source)
    if not _commented(tracker, number, message):
        tracker.comment(number, "failure", message)


def _failure_message(source: str) -> str:
    return _message(source, "the Spec's run failed; the run's ledger and spans hold the details")


def _commented(tracker: Issues, number: int, body: str) -> bool:
    """Whether the issue already carries a Comment that starts with `body` (the tracker appends its marker after it)."""
    return any(comment.body.startswith(body) for comment in tracker.issue(number).comments)

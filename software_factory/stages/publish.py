"""The publication Stage: push the feature branch when it is ahead and ensure its one draft pull request."""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.events.pull_requests import DraftPullRequestPublished
from software_factory.events.specs import SpecRouted, SpecWork
from software_factory.runtime import Stage
from software_factory.shared.git import GitClient
from software_factory.shared.github.account import GitHub
from software_factory.shared.github.issues.client import Issues
from software_factory.shared.github.issues.graph import SpecGraphSnapshot


@dataclass(frozen=True)
class PublishInput:
    work: SpecWork
    routed: SpecRouted
    tracker: Issues
    git: GitClient
    github: GitHub


def publish_draft_pr() -> Stage[PublishInput, DraftPullRequestPublished, DraftPullRequestPublished]:
    """Pushes the feature branch without force when it is ahead and ensures the draft pull request exists once, whatever a crashed run left."""
    return Stage("publish-draft-pr", PublishInput, produces=DraftPullRequestPublished).effect(
        lambda i: _published(i) or _publish(i), reconcile=_published,
    )


def pull_request_body(spec: SpecGraphSnapshot) -> str:
    """The initiative ID on the first line (the code-review initiative gate reads it), then every Ticket and whether it is delivered."""
    lines = [spec.initiative, "", f"Spec: {spec.url}", "", "Tickets:"]
    lines += [f"- [{'x' if ticket.state == 'closed' else ' '}] #{ticket.number} {ticket.title}" for ticket in spec.deliverable_tickets]
    return "\n".join(lines)


def _published(i: PublishInput) -> DraftPullRequestPublished | None:
    """The publication a crashed attempt completed: nothing left to push and a pull request with the current description."""
    routed = i.routed
    if i.git.is_ahead(routed.target.checkout, routed.feature_branch, routed.base_branch):
        return None
    existing = i.github.pull_requests(routed.target.slug).find_open(routed.feature_branch)
    if existing is None or existing.body != pull_request_body(i.tracker.spec(i.work.number)):
        return None
    return DraftPullRequestPublished(existing.url, routed.feature_branch, pushed=False)


def _publish(i: PublishInput) -> DraftPullRequestPublished:
    routed = i.routed
    checkout, branch, base = routed.target.checkout, routed.feature_branch, routed.base_branch
    pushed = i.git.push_if_ahead(checkout, branch, base)
    pulls = i.github.pull_requests(routed.target.slug)
    if not pushed and pulls.find_open(branch) is None and not i.git.remote_branch_exists(checkout, branch):
        return DraftPullRequestPublished(None, branch, pushed=False)
    spec = i.tracker.spec(i.work.number)
    title = f"{spec.initiative}: {spec.bare_title}"
    pull_request = pulls.create_draft_pull_request(branch, base, title, pull_request_body(spec))
    return DraftPullRequestPublished(pull_request.url, branch, pushed)

"""The address Stage: the address agent runs the `address` skill's procedure on the pending review discussion in babysit's worktree; Python checks what it left behind."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loop import AgentWorkspace, Evidence, Fail, Pass, Stage, Verdict
from loop.shared.git import GitClient

from software_factory.src.github import GitHub
from software_factory.src.pull_requests.agents.roles.address import (
    ADDRESS,
    AddressReport,
    AddressRequest,
)
from software_factory.src.pull_requests.events.feedback import (
    DiscussionAddressed,
    DiscussionPending,
)
from software_factory.src.pull_requests.events.pull_requests import PullRequestPolled
from software_factory.src.pull_requests.infrastructure.github.pull_requests.discussion_state import (
    discussion_token,
    gate,
)

type Commands = Callable[[PullRequestPolled], tuple[tuple[str, ...], tuple[str, ...]]]


@dataclass(frozen=True)
class AddressInput:
    pending: DiscussionPending
    polled: PullRequestPolled
    worktree: AgentWorkspace
    git: GitClient
    github: GitHub


def address_discussion(
    *, commands: Commands
) -> Stage[AddressInput, AddressReport, DiscussionAddressed]:
    """The address agent fixes, commits, pushes, replies and resolves; fails when it reports a blocker, leaves commits unpushed, or leaves a pending thread unanswered. `commands` gives the repository's build and test commands."""
    return (
        Stage("address-discussion", AddressInput, produces=DiscussionAddressed)
        .agent(
            ADDRESS,
            request=lambda i, _: _request(i, commands),
            session=lambda i: i.polled.title,
        )
        .gate(_addressed)
        .attributes(lambda i: {"discussion": i.pending.token})
    )


def _url(polled: PullRequestPolled) -> str:
    return f"https://github.com/{polled.target.slug}/pull/{polled.number}"


def _save_discussion(i: AddressInput) -> Path:
    """Writes the gate's JSON outside the worktree, which must stay clean, for the agent to read instead of carrying it in the prompt."""
    slug = i.polled.target.slug.replace("/", "-")
    folder = Path(tempfile.gettempdir()) / "babysit-discussions"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slug}-{i.polled.number}-{i.pending.token}.json"
    path.write_text(json.dumps(i.pending.discussion, indent=1), encoding="utf-8")
    return path


def _request(i: AddressInput, commands: Commands) -> AddressRequest:
    polled = i.polled
    owner, repo = polled.target.slug.split("/", 1)
    build, test = commands(polled)
    base: Any = polled.snapshot["pr"]["base"]  # type: ignore[index]
    return AddressRequest(
        _url(polled),
        owner,
        repo,
        polled.number,
        polled.branch,
        str(base["ref"]),
        i.pending.head,
        build,
        test,
        str(_save_discussion(i)),
    )


def _addressed(
    i: AddressInput, report: AddressReport, _: Evidence
) -> Verdict[DiscussionAddressed]:
    problem = _problem(i, report)
    if problem is not None:
        return Fail(problem)
    after = gate(_url(i.polled))
    if after.get("action") == "stop":
        return Fail(f"Address gate stopped after the run: {after.get('reason')}")
    left = _unanswered(i.pending.discussion, after)
    if left:
        return Fail(f"Address left pending threads unanswered: {', '.join(left)}")
    return Pass(
        DiscussionAddressed(
            (i.pending.token, discussion_token(after)),
            i.git.head(i.worktree.path).sha,
            report.outcome,
            report.reason,
        )
    )


def _problem(i: AddressInput, report: AddressReport) -> str | None:
    """Why the run is refused before the discussion is reread; None when the agent finished and pushed what it committed."""
    if report.outcome == "blocked":
        return f"Address blocked: {report.reason or 'no reason given'}"
    local = i.git.head(i.worktree.path).sha
    if local == i.pending.head:
        return None
    remote = (
        i.github.pull_requests(i.polled.target.slug)
        .pull_request(i.polled.number)
        .head_sha
    )
    return None if remote == local else f"Address left commits unpushed at {local}"


def _unanswered(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, ...]:
    """The threads pending before the run that are still pending on the same latest comment."""
    latest = {
        thread["id"]: thread["comments"][-1]["id"]
        for thread in after.get("threads", [])
        if thread["state"] == "pending" and thread["comments"]
    }
    return tuple(
        thread["id"]
        for thread in before.get("threads", [])
        if thread["state"] == "pending"
        and thread["comments"]
        and latest.get(thread["id"]) == thread["comments"][-1]["id"]
    )

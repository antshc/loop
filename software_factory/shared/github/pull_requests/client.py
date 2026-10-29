"""Pull requests of one repository: typed reads, publication, and the replies and requests made on them."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import replace

from ..repo import GitHubRepo
from .lifecycle import LifecycleAction, LifecycleDecision, PullRequestLifecycleError, PullRequestLifecyclePolicy
from .model import PublishedPullRequest, PullRequest, PullRequestSnapshot, ReviewThread
from .reader import PullRequestReader


_REPLY = ("query=mutation($id:ID!,$body:String!){addPullRequestReviewThreadReply("
          "input:{pullRequestReviewThreadId:$id,body:$body}){comment{id}}}")
_RESOLVE = "query=mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}"
_UNRESOLVE = "query=mutation($id:ID!){unresolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}"


class PullRequests:
    """Reads and mutates pull requests of one repository through `gh`; never merges."""

    def __init__(self, repo: GitHubRepo) -> None:
        self._repo = repo
        self._reader = PullRequestReader(repo)

    @property
    def slug(self) -> str:
        return self._repo.slug

    def create_draft_pull_request(self, head: str, base: str, title: str, body: str = "") -> PublishedPullRequest:
        """One draft PR per head branch: returns the open PR for `head` when there is one, else creates it."""
        existing = self.find_open(head)
        if existing is not None:
            if body and existing.body != body:
                self.update_description(existing.number, body)
                return replace(existing, body=body)
            return existing
        output = self._repo.run(
            ("pr", "create", "--repo", self.slug, "--head", head, "--base", base, "--title", title, "--body", body, "--draft")
        )
        match = re.search(r"/pull/(\d+)", output.strip())
        number = int(match.group(1)) if match else 0
        return PublishedPullRequest(number=number, title=title, url=output.strip(), branch=head, body=body)

    def find_open(self, head: str) -> PublishedPullRequest | None:
        """The open PR whose head is `head`, if any."""
        output = self._repo.run(
            ("pr", "list", "--repo", self.slug, "--state", "open", "--head", head,
             "--json", "number,title,url,headRefName,body,isDraft")
        )
        prs = json.loads(output)
        if not prs:
            return None
        pr = prs[0]
        return PublishedPullRequest(pr["number"], pr["title"], pr["url"], pr["headRefName"], pr.get("body") or "", pr.get("isDraft", True))

    def mark_ready(self, pull_request: str) -> None:
        """Takes a PR out of draft; `pull_request` is its number or URL."""
        self._repo.run(("pr", "ready", pull_request, "--repo", self.slug))

    def diff(self, pull_request: str) -> str:
        """The unified diff of a PR; `pull_request` is its number or URL."""
        return self._repo.run(("pr", "diff", pull_request, "--repo", self.slug))

    def pull_request(self, number: int) -> PullRequest:
        """One PR with GitHub's current mergeability."""
        return self._reader.pull_request(number)

    def open_by(self, author: str) -> tuple[PullRequest, ...]:
        """Every open PR authored by `author`, drafts included."""
        return self._reader.open_by(author)

    def threads(self, number: int) -> tuple[ReviewThread, ...]:
        """All review threads of a PR with all their submitted comments."""
        return self._reader.threads(number)

    def snapshot(self, number: int) -> PullRequestSnapshot:
        """A PR with its review threads, reviews, Comments, and check results; a closed PR is read without them."""
        return self._reader.snapshot(number)

    def reply_to_thread(self, thread_id: str, body: str) -> None:
        """Replies to a review thread, leaving it open."""
        self._repo.json("api", "graphql", "-f", _REPLY, "-f", f"id={thread_id}", "-f", f"body={body}")

    def resolve_thread(self, thread_id: str) -> None:
        """Marks a review thread resolved."""
        self._repo.json("api", "graphql", "-f", _RESOLVE, "-f", f"id={thread_id}")

    def unresolve_thread(self, thread_id: str) -> None:
        """Marks a review thread unresolved."""
        self._repo.json("api", "graphql", "-f", _UNRESOLVE, "-f", f"id={thread_id}")

    def comment(self, number: int, body: str) -> None:
        """Comments on the PR conversation."""
        self._repo.json("api", f"repos/{self.slug}/issues/{number}/comments", "-X", "POST", "-f", f"body={body}")

    def update_description(self, number: int, body: str) -> None:
        """Replaces the PR description with `body`, overwriting any human edit without a conflict check."""
        self._repo.run(("pr", "edit", str(number), "--repo", self.slug, "--body", body))

    def close(self, number: int) -> None:
        """Closes the PR without merging it; closing a closed PR does nothing."""
        self._change(number, LifecycleAction.CLOSE)

    def reopen(self, number: int) -> None:
        """Reopens a closed PR; reopening an open PR does nothing, and reopening a merged one raises PullRequestLifecycleError."""
        self._change(number, LifecycleAction.REOPEN)

    def _change(self, number: int, action: LifecycleAction) -> None:
        decision = PullRequestLifecyclePolicy().decide(self._reader.pull_request(number), action)
        if decision is LifecycleDecision.INVALID:
            raise PullRequestLifecycleError(f"cannot {action} pull request #{number}: it is already merged")
        if decision is LifecycleDecision.VALID:
            self._repo.run(("pr", action.value, str(number), "--repo", self.slug))

    def failed_logs(self, run_ids: Iterable[int]) -> str:
        """The failed-step logs of the given workflow runs."""
        return "\n".join(self._repo.run(("run", "view", str(run_id), "-R", self.slug, "--log-failed")) for run_id in run_ids)

    def rerun_failed(self, run_id: int) -> None:
        """Reruns only the failed jobs of one workflow run."""
        self._repo.run(("run", "rerun", str(run_id), "-R", self.slug, "--failed"))

    def request_copilot(self, number: int) -> None:
        """Requests a Copilot review of the PR."""
        self._repo.run(("pr", "edit", str(number), "-R", self.slug, "--add-reviewer", "@copilot"))

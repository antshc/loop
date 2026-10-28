"""The checkpointed batch transaction: verify and commit each fix, then push and reply exactly once."""

from __future__ import annotations

from pathlib import Path

from loop import WorkflowRun

from workflows.shared.git import GitClient
from workflows.shared.github.pull_requests import PullRequests

from ..model import Blocked, PrState, PrTarget
from ..settings import Repository, Settings
from ..watch.snapshot import babysit_snapshot
from .verification import Verifier


def _reply_exists(snapshot: dict, item: dict, body: str, login: str) -> bool:
    if item["kind"] == "thread":
        thread = next((t for t in snapshot["threads"] if t["id"] == item["thread_id"]), None)
        if thread is None or thread["isResolved"]:
            return True
        source = item["key"].rsplit(":", 1)[-1]
        index = next((i for i, c in enumerate(thread["comments"]) if c["id"] == source), len(thread["comments"]))
        return any((c.get("author") or {}).get("login") == login and c["body"] == body
                   for c in thread["comments"][index + 1:])
    return any((c.get("author") or {}).get("login") == login and c["body"] == body for c in snapshot["other"])


class ChangePublisher:
    """Owns the PR's batch checkpoints; agents never commit or push."""

    def __init__(self, settings: Settings, repository: Repository, target: PrTarget, state: PrState,
                 git: GitClient, pulls: PullRequests, verify: Verifier) -> None:
        self._settings = settings
        self._repository = repository
        self._target = target
        self._state = state
        self._git = git
        self._pulls = pulls
        self._verify = verify

    def head(self, path: Path) -> str:
        return self._git.head(path).sha

    def clean(self, path: Path) -> bool:
        return self._git.is_clean(path)

    def verify(self, path: Path) -> None:
        self._verify(path, self._repository, self._settings.timeout)

    def agent_head(self, path: Path, before: str) -> None:
        """Blocked when the agent moved HEAD."""
        if self.head(path) != before:
            raise Blocked("Agent changed HEAD; Python must own commits")

    def read_only(self, path: Path, before: str) -> None:
        """Blocked when a read-only agent moved HEAD or edited the worktree."""
        self.agent_head(path, before)
        if not self.clean(path):
            raise Blocked("Read-only agent modified the worktree")

    def recover(self, path: Path) -> None:
        """Adopts a commit an interrupted run made, or blocks on anything unrecognized."""
        state, git = self._state, self._git
        batch = state.data["batch"]
        inflight = batch.get("inflight")
        if inflight:
            head = self.head(path)
            if head != inflight["parent"]:
                if (git.text(path, "rev-parse", "HEAD^") != inflight["parent"]
                        or git.text(path, "log", "-1", "--format=%s") != inflight["message"] or not self.clean(path)):
                    raise Blocked("Unrecognized commit in recovery worktree")
                batch["head"] = head
                batch["index"] += 1
                batch["inflight"] = None
                state.save()
            else:
                raise Blocked("Interrupted verification/commit; inspect retained worktree before retrying")
        if not self.clean(path) or self.head(path) != batch["head"]:
            raise Blocked("Unverified edits or unexpected commits retained in recovery worktree")

    def commit_issue(self, path: Path, message: str, replies: dict, flow: WorkflowRun) -> None:
        """Verifies the agent's edits and commits them as the batch's next step."""
        state = self._state
        batch = state.data["batch"]
        if self.clean(path):
            raise Blocked("Agent claimed a fix but produced no changes")
        with flow.phase("verify"):
            self.verify(path)
        batch["inflight"] = {"parent": self.head(path), "message": message}
        batch["replies"].update(replies)
        state.save()
        batch["head"] = self._git.commit(path, message).sha
        batch["index"] += 1
        batch["inflight"] = None
        state.save()

    def _reply(self, number: int, item: dict, body: str) -> None:
        """Replies in the review thread the item came from, or on the PR conversation."""
        if item["kind"] == "thread":
            self._pulls.reply_to_thread(item["thread_id"], body)
        else:
            self._pulls.comment(number, body)

    def publish(self, path: Path, login: str, flow: WorkflowRun) -> None:
        """Pushes the batch without force, confirms it, then posts each reply once."""
        state, pulls, number = self._state, self._pulls, self._target.number
        batch = state.data["batch"]
        with flow.phase("publish", head=batch["head"]):
            current = babysit_snapshot(pulls.snapshot(number))
            if current["pr"]["state"] != "open":
                raise Blocked("PR closed during publication; inspect retained work before retrying")
            if current["pr"]["head"]["ref"] != batch["branch"]:
                raise Blocked("PR branch changed during publication")
            remote_head = current["pr"]["head"]["sha"]
            if remote_head not in {batch["base"], batch["head"]}:
                raise Blocked("PR head moved; publication stopped before replying")
            if batch["head"] != batch["base"] and remote_head != batch["head"]:
                self._git.publish(path, batch["branch"])
            if pulls.pull_request(number).head_sha != batch["head"]:
                raise Blocked("Published SHA could not be confirmed")
            batch["pushed"] = True
            state.save()
            for key, reply in batch["replies"].items():
                item = state.data["items"][key]
                if item["status"] == "handled":
                    continue
                body = reply if item["kind"] == "thread" else f"In response to {item['url']}:\n\n{reply}"
                current = babysit_snapshot(pulls.snapshot(number))
                if item["kind"] == "thread" and not _reply_exists(current, item, body, login):
                    thread = next((thread for thread in current["threads"] if thread["id"] == item["thread_id"]), None)
                    source = item["key"].rsplit(":", 1)[-1]
                    if thread and thread["comments"] and thread["comments"][-1]["id"] != source:
                        item["status"] = "superseded"
                        state.save()
                        continue
                if not _reply_exists(current, item, body, login):
                    self._reply(number, item, body)
                item["status"] = "handled"
                state.save()
            state.data["worktree_base"] = batch["head"]
            state.data["batch"] = None
            state.save()

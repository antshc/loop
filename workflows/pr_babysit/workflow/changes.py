from __future__ import annotations

from pathlib import Path

from ..infrastructure.git import Git
from ..infrastructure.state import Blocked, PrState

def _agent_head(git: Git, path: Path, before: str) -> None:
    if git.head(path) != before:
        raise Blocked("Agent changed HEAD; Python must own commits")


def _read_only(git: Git, path: Path, before: str) -> None:
    _agent_head(git, path, before)
    if not git.clean(path):
        raise Blocked("Read-only agent modified the worktree")


def _recover_commit(state: PrState, git: Git, path: Path) -> None:
    batch = state.data["batch"]
    inflight = batch.get("inflight")
    if inflight:
        head = git.head(path)
        if head != inflight["parent"]:
            if (git.text(path, "rev-parse", "HEAD^") != inflight["parent"]
                    or git.text(path, "log", "-1", "--format=%s") != inflight["message"] or not git.clean(path)):
                raise Blocked("Unrecognized commit in recovery worktree")
            batch["head"] = head
            batch["index"] += 1
            batch["inflight"] = None
            state.save()
        else:
            raise Blocked("Interrupted verification/commit; inspect retained worktree before retrying")
    if not git.clean(path) or git.head(path) != batch["head"]:
        raise Blocked("Unverified edits or unexpected commits retained in recovery worktree")


def _commit_issue(state: PrState, git: Git, path: Path, message: str, replies: dict, flow, repository, timeout, verify) -> None:
    batch = state.data["batch"]
    if git.clean(path):
        raise Blocked("Agent claimed a fix but produced no changes")
    with flow.phase("verify"):
        verify(path, repository, timeout)
    batch["inflight"] = {"parent": git.head(path), "message": message}
    batch["replies"].update(replies)
    state.save()
    batch["head"] = git.commit(path, message)
    batch["index"] += 1
    batch["inflight"] = None
    state.save()


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


def _publish(state, git, path, github, repo, number, login, flow) -> None:
    batch = state.data["batch"]
    with flow.phase("publish", head=batch["head"]):
        current = github.snapshot(repo, number)
        if current["pr"]["state"] != "open":
            raise Blocked("PR closed during publication; inspect retained work before retrying")
        if current["pr"]["head"]["ref"] != batch["branch"]:
            raise Blocked("PR branch changed during publication")
        remote_head = current["pr"]["head"]["sha"]
        if remote_head not in {batch["base"], batch["head"]}:
            raise Blocked("PR head moved; publication stopped before replying")
        if batch["head"] != batch["base"] and remote_head != batch["head"]:
            git.publish(path, batch["branch"])
        confirmed = github.json("api", f"repos/{repo}/pulls/{number}")
        if confirmed["head"]["sha"] != batch["head"]:
            raise Blocked("Published SHA could not be confirmed")
        batch["pushed"] = True
        state.save()
        for key, reply in batch["replies"].items():
            item = state.data["items"][key]
            if item["status"] == "handled":
                continue
            body = reply if item["kind"] == "thread" else f"In response to {item['url']}:\n\n{reply}"
            current = github.snapshot(repo, number)
            if item["kind"] == "thread" and not _reply_exists(current, item, body, login):
                thread = next((thread for thread in current["threads"] if thread["id"] == item["thread_id"]), None)
                source = item["key"].rsplit(":", 1)[-1]
                if thread and thread["comments"] and thread["comments"][-1]["id"] != source:
                    item["status"] = "superseded"
                    state.save()
                    continue
            if not _reply_exists(current, item, body, login):
                github.reply(repo, number, item, body)
            item["status"] = "handled"
            state.save()
        state.data["worktree_base"] = batch["head"]
        state.data["batch"] = None
        state.save()

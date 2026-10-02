#!/usr/bin/env python3
"""Report the answer-state of a pull request's review discussion.

Read-only. Fetches every review thread on a PR, drops the resolved ones, and asks one
question per remaining thread: is its *last* comment authored by the acting user?

  - yes  -> `answered`: this run has nothing to add to it.
  - no   -> `pending`: someone is waiting on a reply.
             `first-pass`  - the acting user has never commented in the thread.
             `follow-up`   - the acting user replied and someone commented after that.

An acting user's own last comment containing `fix!:` bypasses the `answered` state — it's
treated as a self-note that still needs work, not as an answer already given.

The whole discussion coming back `answered` is what makes the calling skill rerunnable:
the run stops at the gate without reading a single comment body.

Comments still attached to an unsubmitted (`PENDING`) review are ignored entirely, so
draft-only activity never makes a thread pending and never reopens an already-answered one.

Usage:
    python3 pr_discussion_state.py <PR URL | owner/repo#N> [--json-out PATH] [--pretty]

Output is one JSON object on stdout carrying `action` ("skip" | "proceed" | "stop"),
the counts, and the pending threads with their comments and source anchors.
Exit 0 for "skip" and "proceed", 1 for "stop" and for any failure.

Requires `gh` on PATH, authenticated against the PR's host.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

PR_URL_RE = re.compile(r"^https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)")
PR_SHORT_RE = re.compile(r"^(?P<owner>[^/\s]+)/(?P<repo>[^/#\s]+)#(?P<number>\d+)$")
FIX_KEYWORD_RE = re.compile(r"fix!:")

THREADS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          startLine
          originalLine
          originalStartLine
          diffSide
          comments(first: 100) {
            totalCount
            pageInfo { hasNextPage }
            nodes { id author { login } body createdAt diffHunk pullRequestReview { state } }
          }
        }
      }
    }
  }
}
"""


class GhError(RuntimeError):
    pass


def run_gh(args: list[str]) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GhError(f"gh {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def parse_pr_ref(raw: str) -> tuple[str, str, int]:
    match = PR_URL_RE.match(raw.strip()) or PR_SHORT_RE.match(raw.strip())
    if not match:
        raise ValueError(
            f"cannot parse {raw!r} — expected https://host/owner/repo/pull/N or owner/repo#N"
        )
    return match["owner"], match["repo"], int(match["number"])


def acting_login() -> str:
    return run_gh(["api", "user", "-q", ".login"]).strip()


def pr_metadata(owner: str, repo: str, number: int) -> dict[str, Any]:
    out = run_gh([
        "pr", "view", str(number),
        "--repo", f"{owner}/{repo}",
        "--json", "headRefName,baseRefName,state,isDraft,url,author",
    ])
    return json.loads(out)


def fetch_threads(owner: str, repo: str, number: int) -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        args = [
            "api", "graphql",
            "-f", f"query={THREADS_QUERY}",
            "-f", f"owner={owner}",
            "-f", f"repo={repo}",
            "-F", f"number={number}",
        ]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        page = json.loads(run_gh(args))
        if page.get("errors"):
            raise GhError(f"GraphQL errors: {json.dumps(page['errors'])}")
        connection = page["data"]["repository"]["pullRequest"]["reviewThreads"]
        threads.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            return threads
        cursor = connection["pageInfo"]["endCursor"]


def resolve_anchor(thread: dict[str, Any]) -> dict[str, Any]:
    """Where the thread points in source, falling back to the pre-outdating position."""
    if thread.get("line") is not None:
        return {
            "path": thread["path"],
            "startLine": thread.get("startLine"),
            "line": thread["line"],
            "side": thread.get("diffSide"),
            "basis": "current",
        }
    if thread.get("originalLine") is not None:
        return {
            "path": thread["path"],
            "startLine": thread.get("originalStartLine"),
            "line": thread["originalLine"],
            "side": thread.get("diffSide"),
            "basis": "original",
        }
    return {"path": thread["path"], "startLine": None, "line": None, "side": thread.get("diffSide"), "basis": "unknown"}


def is_pending_review_comment(comment: dict[str, Any]) -> bool:
    """True for a comment still attached to an unsubmitted (draft) review."""
    review = comment.get("pullRequestReview")
    return bool(review) and review.get("state") == "PENDING"


def flatten_comments(thread: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": comment["id"],
            "author": (comment.get("author") or {}).get("login"),
            "body": comment.get("body", ""),
            "createdAt": comment.get("createdAt"),
        }
        for comment in thread["comments"]["nodes"]
        if not is_pending_review_comment(comment)
    ]


def classify(thread: dict[str, Any], login: str) -> dict[str, Any] | None:
    comments = flatten_comments(thread)
    if not comments:
        return None
    truncated = thread["comments"]["pageInfo"]["hasNextPage"]
    anchor = resolve_anchor(thread)
    first_hunk = next((c.get("diffHunk") for c in thread["comments"]["nodes"] if c.get("diffHunk")), None)

    record: dict[str, Any] = {
        "id": thread["id"],
        "anchor": anchor,
        "isOutdated": thread["isOutdated"],
        "commentCount": thread["comments"]["totalCount"],
        "truncated": truncated,
        "lastAuthor": comments[-1]["author"] if comments else None,
        "lastCommentAt": comments[-1]["createdAt"] if comments else None,
    }

    last_index = len(comments) - 1
    self_fix_bypass = comments[-1]["author"] == login and bool(FIX_KEYWORD_RE.search(comments[-1]["body"]))

    # A truncated thread cannot prove its last comment is ours, so treat it as unanswered.
    if comments and comments[-1]["author"] == login and not truncated and not self_fix_bypass:
        record["state"] = "answered"
        return record

    # The fix!: note itself doesn't count as "my answer already given" — it's new work to surface.
    mine = [
        index
        for index, comment in enumerate(comments)
        if comment["author"] == login and not (self_fix_bypass and index == last_index)
    ]
    record["state"] = "pending"
    record["mode"] = "follow-up" if mine else "first-pass"
    record["diffHunk"] = first_hunk
    record["comments"] = comments
    if mine:
        record["myLastReply"] = comments[mine[-1]]
        record["newComments"] = comments[mine[-1] + 1:]
    return record


def build_state(pr_ref: str) -> tuple[dict[str, Any], int]:
    owner, repo, number = parse_pr_ref(pr_ref)
    login = acting_login()
    meta = pr_metadata(owner, repo, number)

    state: dict[str, Any] = {
        "pr": {"owner": owner, "repo": repo, "number": number, "url": meta.get("url")},
        "actingLogin": login,
        "prState": meta.get("state"),
        "isDraft": meta.get("isDraft"),
        "headRef": meta.get("headRefName"),
        "baseRef": meta.get("baseRefName"),
    }

    if meta.get("state") != "OPEN":
        state["action"] = "stop"
        state["reason"] = f"PR state is {meta.get('state')}, not OPEN"
        state["counts"] = {}
        state["threads"] = []
        return state, 1

    raw_threads = fetch_threads(owner, repo, number)
    resolved = [t for t in raw_threads if t["isResolved"]]
    classified = (classify(t, login) for t in raw_threads if not t["isResolved"])
    live = [record for record in classified if record is not None]

    answered = [t for t in live if t["state"] == "answered"]
    first_pass = [t for t in live if t.get("mode") == "first-pass"]
    follow_up = [t for t in live if t.get("mode") == "follow-up"]

    state["counts"] = {
        "total": len(raw_threads),
        "resolved": len(resolved),
        "answered": len(answered),
        "pendingFirstPass": len(first_pass),
        "pendingFollowUp": len(follow_up),
    }
    state["action"] = "skip" if not (first_pass or follow_up) else "proceed"
    state["threads"] = first_pass + follow_up
    state["answeredThreads"] = [
        {"id": t["id"], "path": t["anchor"]["path"], "lastAuthor": t["lastAuthor"]} for t in answered
    ]
    return state, 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pr", help="PR URL (https://github.com/owner/repo/pull/N) or owner/repo#N")
    parser.add_argument("--json-out", help="also write the JSON state to this path")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON")
    args = parser.parse_args()

    try:
        state, exit_code = build_state(args.pr)
    except (GhError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"action": "stop", "reason": str(error)}))
        return 1

    payload = json.dumps(state, indent=2 if args.pretty else None)
    print(payload)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

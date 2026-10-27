"""Pull requests of one repository: publication, review threads, checks, and CI runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace

from .client import GitHubRepo


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    url: str
    branch: str
    body: str = ""


_THREADS = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){
pageInfo{hasNextPage endCursor} nodes{id isResolved isOutdated path line originalLine
comments(first:100){pageInfo{hasNextPage endCursor} nodes{id databaseId body createdAt diffHunk
 author{login} authorAssociation pullRequestReview{state} url}}}}}}}"""


_COMMENTS = """query($id:ID!,$cursor:String){node(id:$id){... on PullRequestReviewThread{
comments(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{id databaseId body createdAt diffHunk
 author{login} authorAssociation pullRequestReview{state} url}}}}}"""


_REPLY = ("query=mutation($id:ID!,$body:String!){addPullRequestReviewThreadReply("
          "input:{pullRequestReviewThreadId:$id,body:$body}){comment{id}}}")


class PullRequests:
    """Reads and mutates pull requests of one repository through `gh`; never merges."""

    def __init__(self, repo: GitHubRepo) -> None:
        self._repo = repo

    @property
    def slug(self) -> str:
        return self._repo.slug

    def create_draft_pull_request(self, head: str, base: str, title: str, body: str = "") -> PullRequest:
        """One draft PR per head branch: returns the open PR for `head` when there is one, else creates it."""
        existing = self.find_open(head)
        if existing is not None:
            if body and existing.body != body:
                self._repo.run(("pr", "edit", str(existing.number), "--repo", self.slug, "--body", body))
                return replace(existing, body=body)
            return existing
        output = self._repo.run(
            ("pr", "create", "--repo", self.slug, "--head", head, "--base", base, "--title", title, "--body", body, "--draft")
        )
        match = re.search(r"/pull/(\d+)", output.strip())
        number = int(match.group(1)) if match else 0
        return PullRequest(number=number, title=title, url=output.strip(), branch=head, body=body)

    def find_open(self, head: str) -> PullRequest | None:
        """The open PR whose head is `head`, if any."""
        output = self._repo.run(
            ("pr", "list", "--repo", self.slug, "--state", "open", "--head", head,
             "--json", "number,title,url,headRefName,body")
        )
        prs = json.loads(output)
        if not prs:
            return None
        pr = prs[0]
        return PullRequest(pr["number"], pr["title"], pr["url"], pr["headRefName"], pr.get("body") or "")

    def mark_ready(self, pull_request: str) -> None:
        """Takes a PR out of draft; `pull_request` is its number or URL."""
        self._repo.run(("pr", "ready", pull_request, "--repo", self.slug))

    def diff(self, pull_request: str) -> str:
        """The unified diff of a PR; `pull_request` is its number or URL."""
        return self._repo.run(("pr", "diff", pull_request, "--repo", self.slug))

    def open_by(self, author: str) -> list[dict]:
        """Every open PR authored by `author`, drafts included."""
        return [pr for pr in self._repo.pages(f"repos/{self.slug}/pulls?state=open&per_page=100")
                if pr["user"]["login"].lower() == author.lower()]

    def threads(self, number: int) -> list[dict]:
        """All review threads of a PR with all their submitted comments."""
        result, cursor = [], ""
        while True:
            connection = self._repo.json(
                "api", "graphql", "-f", f"query={_THREADS}", "-f", f"owner={self._repo.owner}",
                "-f", f"name={self._repo.name}", "-F", f"number={number}", *(["-f", f"cursor={cursor}"] if cursor else []))
            connection = connection["data"]["repository"]["pullRequest"]["reviewThreads"]
            for thread in connection["nodes"]:
                comments = thread["comments"]
                nodes = list(comments["nodes"])
                while comments["pageInfo"]["hasNextPage"]:
                    response = self._repo.json("api", "graphql", "-f", f"query={_COMMENTS}", "-f", f"id={thread['id']}",
                                               "-f", f"cursor={comments['pageInfo']['endCursor']}")
                    comments = response["data"]["node"]["comments"]
                    nodes.extend(comments["nodes"])
                thread["comments"] = [c for c in nodes if (c.get("pullRequestReview") or {}).get("state") != "PENDING"]
                result.append(thread)
            if not connection["pageInfo"]["hasNextPage"]:
                return result
            cursor = connection["pageInfo"]["endCursor"]

    def snapshot(self, number: int) -> dict:
        """A PR with its review threads, other discussion, checks, statuses, and current-head workflow runs."""
        slug = self.slug
        pr = self._repo.json("api", f"repos/{slug}/pulls/{number}")
        result = {"pr": pr, "threads": [], "other": [], "runs": [], "checks": [], "statuses": []}
        if pr["state"] != "open":
            return result
        sha = pr["head"]["sha"]
        result["review"] = self._repo.json("pr", "view", str(number), "-R", slug, "--json", "reviewDecision,mergeStateStatus")
        result["threads"] = self.threads(number)
        for kind, endpoint in (("comment", f"issues/{number}/comments"), ("review", f"pulls/{number}/reviews")):
            for item in self._repo.pages(f"repos/{slug}/{endpoint}?per_page=100"):
                if item.get("state") == "PENDING" or not item.get("body", "").strip():
                    continue
                result["other"].append({"kind": kind, "id": str(item["id"]), "body": item["body"],
                    "author": item["user"], "author_association": item.get("author_association"),
                    "created_at": item.get("submitted_at") or item.get("created_at"),
                    "updated_at": item.get("updated_at"), "url": item["html_url"]})
        result["checks"] = [c for page in self._repo.json("api", "--paginate", "--slurp",
            f"repos/{slug}/commits/{sha}/check-runs?per_page=100") for c in page["check_runs"]]
        result["statuses"] = self._repo.pages(f"repos/{slug}/commits/{sha}/statuses?per_page=100")
        result["runs"] = [r for page in self._repo.json("api", "--paginate", "--slurp",
            f"repos/{slug}/actions/runs?head_sha={sha}&per_page=100") for r in page["workflow_runs"] if r["head_sha"] == sha]
        return result

    def reply(self, number: int, item: dict, body: str) -> None:
        """Replies to a review thread (leaving it open) or comments on the PR conversation."""
        if item["kind"] == "thread":
            self._repo.json("api", "graphql", "-f", _REPLY, "-f", f"id={item['thread_id']}", "-f", f"body={body}")
        else:
            self._repo.json("api", f"repos/{self.slug}/issues/{number}/comments", "-X", "POST", "-f", f"body={body}")

    def failed_logs(self, runs: list[dict]) -> str:
        """The failed-step logs of the given workflow runs."""
        return "\n".join(self._repo.run(("run", "view", str(run["id"]), "-R", self.slug, "--log-failed")) for run in runs)

    def rerun_failed(self, run_id: int) -> None:
        """Reruns only the failed jobs of one workflow run."""
        self._repo.run(("run", "rerun", str(run_id), "-R", self.slug, "--failed"))

    def request_copilot(self, number: int) -> None:
        """Requests a Copilot review of the PR."""
        self._repo.run(("pr", "edit", str(number), "-R", self.slug, "--add-reviewer", "@copilot"))

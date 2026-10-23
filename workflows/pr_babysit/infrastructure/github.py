from __future__ import annotations

import hashlib
import json
import subprocess
from typing import Callable


THREADS = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){
pageInfo{hasNextPage endCursor} nodes{id isResolved isOutdated path line originalLine
comments(first:100){pageInfo{hasNextPage endCursor} nodes{id databaseId body createdAt diffHunk
 author{login} authorAssociation pullRequestReview{state} url}}}}}}}"""
COMMENTS = """query($id:ID!,$cursor:String){node(id:$id){... on PullRequestReviewThread{
comments(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{id databaseId body createdAt diffHunk
 author{login} authorAssociation pullRequestReview{state} url}}}}}"""
TRUSTED = {"OWNER", "MEMBER", "COLLABORATOR"}
BOTS = {"copilot", "copilot-pull-request-reviewer", "chatgpt-codex-connector", "coderabbitai", "claude", "bugbot"}


def trusted(comment: dict, login: str) -> bool:
    """Accept repository collaborators, the operator, and known review automation."""
    author = comment.get("author", {})
    name = author.get("login", "") if isinstance(author, dict) else author
    return (name == login or comment.get("authorAssociation", comment.get("author_association")) in TRUSTED
            or name.lower().removesuffix("[bot]") in BOTS)


def digest(value: object) -> str:
    """Produce a stable key for remote work and blocked-run recovery."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Github:
    """Read GitHub state and perform explicit workflow-owned mutations via gh."""

    def __init__(self, *, run: Callable = subprocess.run) -> None:
        self._run = run

    def text(self, *args: str) -> str:
        """Execute one bounded GitHub CLI command."""
        return self._run(["gh", *args], check=True, capture_output=True, text=True, timeout=120).stdout

    def json(self, *args: str):
        """Decode a GitHub CLI JSON response."""
        value = json.loads(self.text(*args))
        if isinstance(value, dict) and value.get("errors"):
            raise ValueError(f"GitHub API errors: {value.get('errors')}")
        return value

    def pages(self, endpoint: str) -> list[dict]:
        """Collect every page of a REST collection."""
        pages = self.json("api", "--paginate", "--slurp", endpoint)
        return [item for page in pages for item in page]

    def login(self) -> str:
        """Resolve the authenticated actor used by the discussion gate."""
        return self.json("api", "user")["login"]

    def open_prs(self, repo: str, author: str) -> list[dict]:
        """Discover all open PRs authored by the configured user, including drafts."""
        return [pr for pr in self.pages(f"repos/{repo}/pulls?state=open&per_page=100")
                if pr["user"]["login"].lower() == author.lower()]

    def threads(self, repo: str, number: int) -> list[dict]:
        """Read all review threads and all their submitted comments."""
        owner, name = repo.split("/")
        result, cursor = [], ""
        while True:
            connection = self.json("api", "graphql", "-f", f"query={THREADS}", "-f", f"owner={owner}",
                                   "-f", f"name={name}", "-F", f"number={number}",
                                   *(["-f", f"cursor={cursor}"] if cursor else []))
            if connection.get("errors"):
                raise ValueError(f"Cannot fetch review threads: {connection['errors']}")
            connection = connection["data"]["repository"]["pullRequest"]["reviewThreads"]
            for thread in connection["nodes"]:
                comments = thread["comments"]
                nodes = list(comments["nodes"])
                while comments["pageInfo"]["hasNextPage"]:
                    response = self.json("api", "graphql", "-f", f"query={COMMENTS}", "-f", f"id={thread['id']}",
                                         "-f", f"cursor={comments['pageInfo']['endCursor']}")
                    if response.get("errors"):
                        raise ValueError(f"Cannot fetch thread comments: {response['errors']}")
                    comments = response["data"]["node"]["comments"]
                    nodes.extend(comments["nodes"])
                thread["comments"] = [c for c in nodes if (c.get("pullRequestReview") or {}).get("state") != "PENDING"]
                result.append(thread)
            if not connection["pageInfo"]["hasNextPage"]:
                return result
            cursor = connection["pageInfo"]["endCursor"]

    def snapshot(self, repo: str, number: int) -> dict:
        """Read a PR, discussion gate inputs, checks, and current-SHA workflow runs."""
        pr = self.json("api", f"repos/{repo}/pulls/{number}")
        result = {"pr": pr, "threads": [], "other": [], "runs": [], "checks": [], "statuses": []}
        if pr["state"] != "open":
            return result
        sha = pr["head"]["sha"]
        result["review"] = self.json("pr", "view", str(number), "-R", repo,
                                          "--json", "reviewDecision,mergeStateStatus")
        result["threads"] = self.threads(repo, number)
        for kind, endpoint in (("comment", f"issues/{number}/comments"), ("review", f"pulls/{number}/reviews")):
            for item in self.pages(f"repos/{repo}/{endpoint}?per_page=100"):
                if item.get("state") == "PENDING" or not item.get("body", "").strip():
                    continue
                result["other"].append({"kind": kind, "id": str(item["id"]), "body": item["body"],
                    "author": item["user"], "author_association": item.get("author_association"),
                    "created_at": item.get("submitted_at") or item.get("created_at"),
                    "updated_at": item.get("updated_at"), "url": item["html_url"]})
        result["checks"] = [c for page in self.json("api", "--paginate", "--slurp",
            f"repos/{repo}/commits/{sha}/check-runs?per_page=100") for c in page["check_runs"]]
        result["statuses"] = self.pages(f"repos/{repo}/commits/{sha}/statuses?per_page=100")
        result["runs"] = [r for page in self.json("api", "--paginate", "--slurp",
            f"repos/{repo}/actions/runs?head_sha={sha}&per_page=100") for r in page["workflow_runs"] if r["head_sha"] == sha]
        return result

    def reply(self, repo: str, number: int, item: dict, body: str) -> None:
        """Post a plain reply while leaving the review thread open."""
        if item["kind"] == "thread":
            self.json("api", "graphql", "-f", "query=mutation($id:ID!,$body:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$id,body:$body}){comment{id}}}",
                      "-f", f"id={item['thread_id']}", "-f", f"body={body}")
        else:
            self.json("api", f"repos/{repo}/issues/{number}/comments", "-X", "POST", "-f", f"body={body}")

    def logs(self, repo: str, runs: list[dict]) -> str:
        """Collect failed workflow logs for maintainer diagnosis."""
        return "\n".join(self.text("run", "view", str(run["id"]), "-R", repo, "--log-failed") for run in runs)

    def retry(self, repo: str, run_id: int) -> None:
        """Rerun only the failed jobs of one diagnosed workflow run."""
        self.text("run", "rerun", str(run_id), "-R", repo, "--failed")

    def request_copilot(self, repo: str, number: int) -> None:
        """Request Copilot review only when explicitly enabled by configuration."""
        self.text("pr", "edit", str(number), "-R", repo, "--add-reviewer", "@copilot")


def remote_fingerprint(snapshot: dict) -> str:
    """Identify changed review and CI inputs without scheduler telemetry."""
    return digest({"sha": snapshot["pr"]["head"]["sha"], "state": snapshot["pr"]["state"],
                   "threads": snapshot["threads"], "other": snapshot["other"],
                   "checks": [(c["id"], c["status"], c.get("conclusion")) for c in snapshot["checks"]],
                   "statuses": [(s["id"], s["state"]) for s in snapshot["statuses"]],
                   "runs": [(r["id"], r["status"], r.get("conclusion"), r.get("run_attempt")) for r in snapshot["runs"]]})

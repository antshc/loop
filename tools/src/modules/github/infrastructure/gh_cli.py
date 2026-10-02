"""Thin wrapper around the `gh` CLI for subprocess execution."""

from __future__ import annotations

import json
import subprocess

_ISSUE_COMMENTS_LIMIT = 20
_REVIEW_THREADS_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          id isResolved path line startLine
          comments(first: 50) {
            nodes { author { login } body }
          }
        }
      }
    }
  }
}
"""
_ISSUE_FIELDS = (
    "number title body url state "
    "labels(first: 20) { nodes { name } } "
    f"comments(first: {_ISSUE_COMMENTS_LIMIT}) {{ nodes {{ id body createdAt }} }}"
)
_OPEN_ISSUES_QUERY = """
query($owner: String!, $repo: String!, $endCursor: String) {{
  repository(owner: $owner, name: $repo) {{
    issues(first: 100, states: OPEN, after: $endCursor) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ {fields} }}
    }}
  }}
}}
""".format(fields=_ISSUE_FIELDS)
_SPEC_ISSUES_QUERY = """
query($owner: String!, $repo: String!, $endCursor: String) {{
  repository(owner: $owner, name: $repo) {{
    issues(first: 100, states: OPEN, labels: ["spec"], after: $endCursor) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ {fields} }}
    }}
  }}
}}
""".format(fields=_ISSUE_FIELDS)
_SUB_ISSUES_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $endCursor: String) {{
  repository(owner: $owner, name: $repo) {{
    issue(number: $number) {{
      subIssues(first: 100, after: $endCursor) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ {fields} }}
      }}
    }}
  }}
}}
""".format(fields=_ISSUE_FIELDS)


class GhCli:
    """Executes `gh` CLI commands and returns raw data."""

    def pr_list(self, user: str, repo: str) -> list[dict]:
        """Run `gh pr list` and return parsed JSON list of PR objects."""
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--repo", repo,
                "--author", user,
                "--state", "open",
                "--json", "url,title",
            ],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)

    def fetch_threads_raw(self, owner: str, repo: str, number: int) -> list[dict]:
        """Run `gh api graphql` for review threads and return flattened nodes."""
        cmd = [
            "gh", "api", "graphql",
            "-f", f"query={_REVIEW_THREADS_QUERY}",
            "-f", f"owner={owner}",
            "-f", f"repo={repo}",
            "-F", f"number={number}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        nodes = data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        for node in nodes:
            node["comments"] = node["comments"]["nodes"]
        return nodes

    def fetch_issues_raw(self, owner: str, repo: str, spec_number: int | None = None) -> list[dict]:
        """Run `gh api graphql` for open issues — the open sub-issues of *spec_number* when given."""
        if spec_number is None:
            pages = self._graphql_pages(_OPEN_ISSUES_QUERY, owner, repo)
            nodes = [node for page in pages for node in page["data"]["repository"]["issues"]["nodes"]]
        else:
            pages = self._graphql_pages(_SUB_ISSUES_QUERY, owner, repo, number=spec_number)
            nodes = [
                node for page in pages
                for node in page["data"]["repository"]["issue"]["subIssues"]["nodes"]
                if node["state"] == "OPEN"
            ]
        return [self._flatten_issue(node) for node in nodes]

    def list_specs_raw(self, owner: str, repo: str) -> list[dict]:
        """Run `gh api graphql` for open issues labelled `spec` and return flattened nodes."""
        pages = self._graphql_pages(_SPEC_ISSUES_QUERY, owner, repo)
        nodes = [node for page in pages for node in page["data"]["repository"]["issues"]["nodes"]]
        return [self._flatten_issue(node) for node in nodes]

    def _graphql_pages(self, query: str, owner: str, repo: str, *, number: int | None = None) -> list[dict]:
        cmd = [
            "gh", "api", "graphql", "--paginate", "--slurp",
            "-f", f"query={query}",
            "-f", f"owner={owner}",
            "-f", f"repo={repo}",
        ]
        if number is not None:
            cmd += ["-F", f"number={number}"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    @staticmethod
    def _flatten_issue(node: dict) -> dict:
        node["labels"] = [label["name"] for label in node["labels"]["nodes"]]
        node["comments"] = node["comments"]["nodes"]
        return node

    def pr_checkout(self, pr_url: str) -> None:
        """Run `gh pr checkout` for the given PR URL."""
        subprocess.run(["gh", "pr", "checkout", pr_url], check=True)

"""GitHub access, work tracking, and pull requests for dev."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .commands import CommandError, cli_runner, run_command
from .records import (
    Comment, PlannedTicket, PullRequest, Repository, Spec, Ticket, TicketWorkflowError, _AddLabel, _Change, _CommentOn,
)


_SLUG_REMOTE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


class GhCli:
    """Wraps one `gh` invocation; the only place `gh` is started."""

    def __init__(self, *, cwd: Path | None = None) -> None:
        self._call = cli_runner("gh", cwd=cwd)

    def __call__(self, args: tuple[str, ...]) -> str:
        return self._call(args)


GhRunner = Callable[[tuple[str, ...]], str]


class GitHubRepo:
    """One GitHub repository reached through `gh`; the shared seam under the issue and pull-request clients."""

    def __init__(self, owner: str, name: str, *, gh: GhRunner) -> None:
        self.owner = owner
        self.name = name
        self._gh = gh

    @classmethod
    def from_origin(cls, checkout: Path) -> GitHubRepo:
        """The repo behind `checkout`'s `origin` remote; ValueError when it is not a GitHub remote."""
        url = run_command(("git", "remote", "get-url", "origin"), cwd=checkout).strip()
        match = _SLUG_REMOTE.search(url)
        if match is None:
            raise ValueError(f"unsupported remote: {url}")
        return cls(match["owner"], match["repo"], gh=GhCli(cwd=checkout))

    @property
    def slug(self) -> str:
        """`owner/name`, as `gh --repo` expects it."""
        return f"{self.owner}/{self.name}"

    def run(self, args: tuple[str, ...]) -> str:
        """Runs `gh <args>` and returns its stdout."""
        return self._gh(args)

    def graphql(self, query: str, *, paginate: bool = False, scoped: bool = True, **variables: str | int) -> str:
        """Runs a GraphQL query; `scoped` also passes the `$owner`/`$repo` variables."""
        args = ["api", "graphql"]
        if paginate:
            args += ["--paginate", "--slurp"]
        args += ["-f", f"query={query}"]
        if scoped:
            args += ["-f", f"owner={self.owner}", "-f", f"repo={self.name}"]
        for name, value in variables.items():
            flag = "-F" if isinstance(value, int) else "-f"
            args += [flag, f"{name}={value}"]
        return self._gh(tuple(args))


class PullRequestClient:
    """Creates draft PRs or reuses open PRs in one repository through `gh`."""

    def __init__(self, repo: GitHubRepo) -> None:
        self._repo = repo

    def create_draft_pull_request(self, head: str, base: str, title: str, body: str = "") -> PullRequest:
        """One draft PR per head branch: returns the open PR for `head` when there is one, else creates it."""
        existing = self._find_pull_request(head)
        if existing is not None:
            if body and existing.body != body:
                self._repo.run(("pr", "edit", str(existing.number), "--repo", self._repo.slug, "--body", body))
                return replace(existing, body=body)
            return existing
        output = self._repo.run(
            (
                "pr", "create",
                "--repo", self._repo.slug,
                "--head", head,
                "--base", base,
                "--title", title,
                "--body", body,
                "--draft",
            )
        )
        match = re.search(r"/pull/(\d+)", output.strip())
        number = int(match.group(1)) if match else 0
        return PullRequest(number=number, title=title, url=output.strip(), branch=head, body=body)

    def mark_ready(self, pull_request: str) -> None:
        """Takes a PR out of draft; `pull_request` is its number or URL."""
        self._repo.run(("pr", "ready", pull_request, "--repo", self._repo.slug))

    def _find_pull_request(self, head_branch: str) -> PullRequest | None:
        output = self._repo.run(
            (
                "pr", "list",
                "--repo", self._repo.slug,
                "--state", "open",
                "--head", head_branch,
                "--json", "number,title,url,headRefName,body",
            )
        )
        prs = json.loads(output)
        if not prs:
            return None
        pr = prs[0]
        return PullRequest(number=pr["number"], title=pr["title"], url=pr["url"], branch=pr["headRefName"], body=pr.get("body") or "")


_ISSUE_FIELDS = (
    "number title url state body labels(first: 20) { nodes { name } }"
    " comments(first: 50) { nodes { author { login } body createdAt } }"
)


_SPECS_QUERY = (
    "query($owner: String!, $repo: String!) {"
    " repository(owner: $owner, name: $repo) {"
    f'  issues(first: 100, states: OPEN, labels: ["spec"]) {{ nodes {{ {_ISSUE_FIELDS} }} }}'
    " } }"
)


_SUB_ISSUES_QUERY = (
    "query($owner: String!, $repo: String!, $number: Int!) {"
    " repository(owner: $owner, name: $repo) {"
    f"  issue(number: $number) {{ subIssues(first: 100) {{ nodes {{ {_ISSUE_FIELDS} }} }} }}"
    " } }"
)


def _comments(node: dict[str, Any]) -> tuple[Comment, ...]:
    return tuple(
        # `author` is null for deleted accounts.
        Comment((comment["author"] or {}).get("login", "ghost"), comment["body"], comment["createdAt"])
        for comment in node["comments"]["nodes"]
    )


def _labels(node: dict[str, Any]) -> tuple[str, ...]:
    return tuple(label["name"] for label in node["labels"]["nodes"])


def _with_blockers(planned: PlannedTicket, earlier: list[int]) -> str:
    blockers = [f"#{earlier[index]}" for index in planned.blocked_by if 0 <= index < len(earlier)]
    return f"{planned.body.rstrip()}\n\nBlocked by {', '.join(blockers)}" if blockers else planned.body


def _spec(node: dict[str, Any], tickets: tuple[Ticket, ...], total: int = 0, held: tuple[int, ...] = ()) -> Spec:
    return Spec(
        number=node["number"],
        title=node["title"],
        url=node["url"],
        labels=_labels(node),
        body=node["body"],
        comments=_comments(node),
        tickets=tickets,
        sub_issue_total=total,
        held_tickets=held,
    )


def _ticket(node: dict[str, Any]) -> Ticket:
    return Ticket(
        number=node["number"],
        title=node["title"],
        state=node["state"].lower(),
        labels=_labels(node),
        url=node["url"],
        body=node["body"],
        comments=_comments(node),
    )


class TicketsTracker:
    """The harness repo's Spec/Ticket tracker: loads Specs through `gh` and persists the changes made to them."""

    def __init__(self, owner: str, name: str, *, gh: GhRunner) -> None:
        self._owner = owner
        self._name = name
        self._gh = gh

    def specs(self) -> Iterable[Spec]:
        """Every open Spec on the harness tracker, each with its deliverable Tickets."""
        specs = []
        for node in self._spec_issues():
            tickets = tuple(_ticket(item) for item in self._sub_issues(node["number"]))
            actionable = tuple(ticket for ticket in tickets if ticket.actionable)
            held = tuple(ticket.number for ticket in tickets if ticket.state == "open" and not ticket.actionable)
            specs.append(_spec(node, actionable, len(tickets), held))
        return specs

    def update_spec(self, spec: Spec) -> None:
        """Persists the Spec's and its Tickets' pending changes in order; a failed change stays pending."""
        for change in spec.pending_changes:
            self._apply(change)
            spec.acknowledge(change)

    def create_tickets(self, spec: Spec, planned: Iterable[PlannedTicket]) -> tuple[Ticket, ...]:
        """Creates each planned Ticket as an open sub-issue of `spec`, in order, and adds them to `spec.tickets`.

        `blocked_by` indexes become `Blocked by #N` lines in the body. A failure raises
        `TicketWorkflowError` naming the sub-issues already created, which stay on the tracker.
        """
        slug = f"{self._owner}/{self._name}"
        created: list[Ticket] = []
        try:
            for planned_ticket in planned:
                body = _with_blockers(planned_ticket, [ticket.number for ticket in created])
                output = self._gh(("issue", "create", "--repo", slug, "--title", planned_ticket.title, "--body", body)).strip()
                match = re.search(r"/issues/(\d+)", output)
                if match is None:
                    raise TicketWorkflowError(f"cannot read the number of the created issue from {output!r}")
                number = int(match.group(1))
                issue_id = self._gh(("api", f"repos/{slug}/issues/{number}", "--jq", ".id")).strip()
                self._gh(("api", "--method", "POST", f"repos/{slug}/issues/{spec.number}/sub_issues", "-F", f"sub_issue_id={issue_id}"))
                created.append(Ticket(number, planned_ticket.title, "open", (), output, body))
        except (CommandError, TicketWorkflowError) as exception:
            made = ", ".join(f"#{ticket.number}" for ticket in created) or "none"
            raise TicketWorkflowError(f"creating Tickets failed after creating {made}: {exception}") from exception
        spec.tickets = (*spec.tickets, *created)
        spec.sub_issue_total += len(created)
        return tuple(created)

    def _tickets(self, spec_number: int) -> tuple[Ticket, ...]:
        tickets = (_ticket(node) for node in self._sub_issues(spec_number))
        return tuple(ticket for ticket in tickets if ticket.actionable)

    def _spec_issues(self) -> list[dict[str, Any]]:
        pages = json.loads(self._graphql(_SPECS_QUERY))
        return [node for page in pages for node in page["data"]["repository"]["issues"]["nodes"]]

    def _sub_issues(self, number: int) -> list[dict[str, Any]]:
        pages = json.loads(self._graphql(_SUB_ISSUES_QUERY, number=number))
        return [node for page in pages for node in page["data"]["repository"]["issue"]["subIssues"]["nodes"]]

    def _graphql(self, query: str, **variables: int) -> str:
        args = ["api", "graphql", "--paginate", "--slurp", "-f", f"query={query}"]
        args += ["-f", f"owner={self._owner}", "-f", f"repo={self._name}"]
        for name, value in variables.items():
            args += ["-F", f"{name}={value}"]
        return self._gh(tuple(args))

    def _apply(self, change: _Change) -> None:
        slug = f"{self._owner}/{self._name}"
        number = str(change.number)
        if isinstance(change, _CommentOn):
            self._gh(("issue", "comment", number, "--repo", slug, "--body", change.body))
        elif isinstance(change, _AddLabel):
            self._gh(("issue", "edit", number, "--repo", slug, "--add-label", change.label))
        else:
            self._gh(("issue", "close", number, "--repo", slug, "--comment", change.comment))


GithubFactory = Callable[[Path], GitHubRepo]


def github_for(repository: Repository, factory: GithubFactory | None) -> GitHubRepo:
    """Creates the repository client, using the injected factory when supplied."""
    if factory is not None:
        return factory(repository.checkout)
    return GitHubRepo(
        repository.owner_name.owner,
        repository.owner_name.repo,
        gh=GhCli(cwd=repository.checkout),
    )

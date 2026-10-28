"""Complete reads of issues, Specs, and their Tickets: every list is followed to its last page, and a part that cannot be read is reported, never returned empty."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from ...process import CommandError
from ..identity import CommentRef, IssueIdentity, IssueRef, RepositoryId
from ..repo import GitHubRepo
from .errors import IssueError
from .graph import IssueRelation, SpecGraphSnapshot
from .issue import CloseReason, Comment, GapState, Issue, IssueState, ReadGap
from .issue_classifier import IssueClassifier, IssueKind, LabelTypeDisagreement
from .spec import SPEC_LABEL, Spec, Ticket


logger = logging.getLogger("workflow.issues")


_PER_PAGE = "per_page=100"


# gh reports the HTTP status of a refused request in its error output.
_DENIED = ("HTTP 403", "HTTP 404")


def _denied(exception: CommandError) -> bool:
    return any(status in exception.output for status in _DENIED)


class IssueReader:
    """Reads one repository's issues through `gh`; never returns pull requests as issues."""

    def __init__(self, repo: GitHubRepo, *, classifier: IssueClassifier | None = None) -> None:
        self._repo = repo
        self._classifier = classifier or IssueClassifier()

    @property
    def repository(self) -> RepositoryId:
        """The repository this reader addresses."""
        return RepositoryId(self._repo.owner, self._repo.name)

    def issue(self, number: int) -> Issue:
        """The issue with its labels and Comments; raises when `number` is a pull request or cannot be read."""
        node = self._repo.json("api", self._endpoint(f"issues/{number}"))
        if "pull_request" in node:
            raise IssueError(f"#{number} is a pull request, not an issue")
        return self._hydrate(node)

    def specs(self) -> tuple[SpecGraphSnapshot, ...]:
        """Every open issue labeled `spec`, each loaded with its Tickets; a listing that cannot be read raises."""
        snapshots = []
        for node in self._issues(f"state=open&labels={quote(SPEC_LABEL, safe='')}"):
            issue = self._hydrate(node)
            classification = self._classifier.classify(issue)
            if classification.kind is IssueKind.SPEC:
                snapshots.append(self._graph(Spec.of(issue), node, classification.disagreement))
        return tuple(snapshots)

    def open_issues(self, label: str) -> tuple[Issue, ...]:
        """Every open issue carrying `label`, without its Comments; pull requests are left out and an unreadable listing raises."""
        return tuple(self._summary(node) for node in self._issues(f"state=open&labels={quote(label, safe='')}"))

    def sub_issue_numbers(self, number: int) -> frozenset[int]:
        """The numbers of every sub-issue of `number`, open or closed; an unreadable listing raises."""
        return frozenset(node["number"] for node in self._pages(self._endpoint(f"issues/{number}/sub_issues?{_PER_PAGE}")))

    def created(self, node: dict[str, Any]) -> Issue:
        """The issue a create call returned, as a fresh snapshot with no Comments."""
        return self._summary(node)

    def posted(self, issue: IssueRef, node: dict[str, Any]) -> Comment:
        """The Comment a post call returned."""
        return self._comment(issue, node)

    def _issues(self, query: str) -> list[dict[str, Any]]:
        nodes = self._pages(self._endpoint(f"issues?{query}&{_PER_PAGE}"))
        return [node for node in nodes if "pull_request" not in node]

    def _graph(self, spec: Spec, node: dict[str, Any], disagreement: LabelTypeDisagreement | None) -> SpecGraphSnapshot:
        if disagreement is not None:
            logger.warning("spec #%s: %s", spec.number, disagreement)
        gaps: list[ReadGap] = []
        sub_issues = self._collect(gaps, "sub_issues", self._endpoint(f"issues/{spec.number}/sub_issues?{_PER_PAGE}"))
        sub_issues = [sub_issue for sub_issue in sub_issues if "pull_request" not in sub_issue]
        self._check_total(gaps, "sub_issues", len(sub_issues), (node.get("sub_issues_summary") or {}).get("total"))
        tickets = tuple(Ticket.of(self._hydrate(sub_issue)) for sub_issue in sub_issues if sub_issue["state"] == "open")
        return SpecGraphSnapshot(
            spec=spec,
            tickets=tickets,
            relations=tuple(IssueRelation(spec.ref, ticket.ref) for ticket in tickets),
            sub_issue_total=len(sub_issues),
            held_tickets=tuple(ticket.number for ticket in tickets if not ticket.actionable),
            gaps=tuple(gaps),
            disagreement=disagreement,
        )

    def _hydrate(self, node: dict[str, Any]) -> Issue:
        ref = self._ref(node)
        gaps: list[ReadGap] = []
        labels = self._collect(gaps, "labels", self._endpoint(f"issues/{ref.number}/labels?{_PER_PAGE}"))
        if gaps:
            # The labels the issue payload itself carries stand in, so a Spec whose label list failed is still found; the gap flags it.
            labels = node.get("labels", [])
        comments = self._collect(gaps, "comments", self._endpoint(f"issues/{ref.number}/comments?{_PER_PAGE}"))
        self._check_total(gaps, "comments", len(comments), node.get("comments"))
        issue = self._summary(node, labels=tuple(label["name"] for label in labels))
        return replace(issue, comments=tuple(self._comment(ref, comment) for comment in comments), gaps=tuple(gaps))

    def _summary(self, node: dict[str, Any], *, labels: tuple[str, ...] | None = None) -> Issue:
        state = IssueState(node["state"])
        reason = node.get("state_reason")
        return Issue(
            ref=self._ref(node),
            identity=IssueIdentity(node["id"], node["node_id"]),
            title=node["title"],
            body=node.get("body") or "",
            url=node["html_url"],
            state=state,
            close_reason=CloseReason(reason) if state is IssueState.CLOSED and reason in CloseReason else None,
            labels=labels if labels is not None else tuple(label["name"] for label in node.get("labels", ())),
            assignees=tuple(assignee["login"] for assignee in node.get("assignees") or ()),
            issue_type=(node.get("type") or {}).get("name"),
        )

    def _comment(self, issue: IssueRef, node: dict[str, Any]) -> Comment:
        # `user` is null for deleted accounts.
        author = (node.get("user") or {}).get("login", "ghost")
        return Comment(CommentRef(issue, node["id"]), author, node["body"], node["created_at"])

    def _ref(self, node: dict[str, Any]) -> IssueRef:
        return IssueRef(self.repository, node["number"])

    def _collect(self, gaps: list[ReadGap], part: str, endpoint: str) -> list[dict[str, Any]]:
        """Every item of a list, or nothing and a gap saying why the list could not be read."""
        try:
            return self._pages(endpoint)
        except CommandError as exception:
            state = GapState.UNKNOWN if _denied(exception) else GapState.INCOMPLETE
            gaps.append(ReadGap(part, state, str(exception)))
        except ValueError as exception:
            gaps.append(ReadGap(part, GapState.INCOMPLETE, f"unreadable page: {exception}"))
        return []

    @staticmethod
    def _check_total(gaps: list[ReadGap], part: str, read: int, expected: int | None) -> None:
        # GitHub's own count of the list; fewer items than it counts means a page was cut short.
        if expected is not None and read < expected and not any(gap.part == part for gap in gaps):
            gaps.append(ReadGap(part, GapState.INCOMPLETE, f"read {read} of {expected}"))

    def _pages(self, endpoint: str) -> list[dict[str, Any]]:
        pages = self._repo.json("api", "--paginate", "--slurp", endpoint)
        if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
            raise ValueError(f"expected a list of pages from {endpoint}")
        return [item for page in pages for item in page]

    def _endpoint(self, path: str) -> str:
        return f"repos/{self._repo.slug}/{path}"

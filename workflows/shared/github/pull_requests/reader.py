"""Complete reads of pull requests: every list is followed to its last page, and review comments stay apart from conversation Comments."""

from __future__ import annotations

from typing import Any

from ..identity import IssueIdentity, IssueRef
from ..issues.issue import Comment, comment_from_rest
from ..repo import GitHubRepo
from .model import (
    CheckKind,
    CheckOutcome,
    CheckResult,
    Mergeability,
    MergeState,
    PullRequest,
    PullRequestReview,
    PullRequestSnapshot,
    PullRequestState,
    ReviewComment,
    ReviewDecision,
    ReviewThread,
    ReviewVerdict,
)


_THREADS = """query($owner:String!,$name:String!,$number:Int!,$cursor:String){
repository(owner:$owner,name:$name){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){
pageInfo{hasNextPage endCursor} nodes{id isResolved isOutdated path line originalLine
comments(first:100){pageInfo{hasNextPage endCursor} nodes{id databaseId body createdAt diffHunk
 author{login} authorAssociation pullRequestReview{state} url}}}}}}}"""


_COMMENTS = """query($id:ID!,$cursor:String){node(id:$id){... on PullRequestReviewThread{
comments(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{id databaseId body createdAt diffHunk
 author{login} authorAssociation pullRequestReview{state} url}}}}}"""


_PER_PAGE = "per_page=100"


_PASSED = {"success", "neutral", "skipped"}


_STATUS_OUTCOMES = {"success": CheckOutcome.PASSED, "pending": CheckOutcome.PENDING}


class PullRequestReader:
    """Reads one repository's pull requests through `gh`; a read that cannot complete raises instead of returning less."""

    def __init__(self, repo: GitHubRepo) -> None:
        self._repo = repo

    def pull_request(self, number: int) -> PullRequest:
        """The pull request itself, with GitHub's current mergeability."""
        return self._pull(self._repo.json("api", self._repo.endpoint(f"pulls/{number}")))

    def open_by(self, author: str) -> tuple[PullRequest, ...]:
        """Every open pull request authored by `author`, drafts included; a listing reports no mergeability, so it is unknown."""
        nodes = self._repo.pages(self._repo.endpoint(f"pulls?state=open&{_PER_PAGE}"))
        pulls = (self._pull(node) for node in nodes)
        return tuple(pull for pull in pulls if pull.author.lower() == author.lower())

    def threads(self, number: int) -> tuple[ReviewThread, ...]:
        """Every review thread with every submitted comment, across thread pages and comment pages."""
        result, cursor = [], ""
        while True:
            connection = self._repo.json(
                "api", "graphql", "-f", f"query={_THREADS}", "-f", f"owner={self._repo.owner}",
                "-f", f"name={self._repo.name}", "-F", f"number={number}", *(["-f", f"cursor={cursor}"] if cursor else []))
            connection = connection["data"]["repository"]["pullRequest"]["reviewThreads"]
            result.extend(self._thread(node) for node in connection["nodes"])
            if not connection["pageInfo"]["hasNextPage"]:
                return tuple(result)
            cursor = connection["pageInfo"]["endCursor"]

    def snapshot(self, number: int) -> PullRequestSnapshot:
        """A pull request with its review threads, reviews, Comments, and check results; a closed one is read without them."""
        pull = self.pull_request(number)
        if not pull.is_open:
            return PullRequestSnapshot(pull)
        summary = self._repo.json("pr", "view", str(number), "-R", self._repo.slug, "--json", "reviewDecision,mergeStateStatus")
        threads = self.threads(number)
        comments = self._comments(pull.ref)
        reviews = self._reviews(number)
        sha = pull.head_sha
        previous = self._previous_head(number, sha)
        # Results on the commit before the head are the only way a check that has not re-run on the head shows as stale instead of missing.
        earlier = [*self._check_runs(previous), *self._statuses(previous)] if previous else []
        return PullRequestSnapshot(
            pull_request=pull,
            threads=threads,
            reviews=reviews,
            comments=comments,
            checks=(*self._check_runs(sha), *self._statuses(sha), *self._workflow_runs(sha), *earlier),
            review_decision=ReviewDecision(summary.get("reviewDecision") or ""),
            merge_state_status=MergeState(summary.get("mergeStateStatus") or ""),
        )

    def _pull(self, node: dict[str, Any]) -> PullRequest:
        head = node["head"]
        # `repo` is null once the fork a pull request came from is deleted.
        head_repository = (head.get("repo") or {}).get("full_name")
        return PullRequest(
            ref=IssueRef(self._repo.repository, node["number"]),
            identity=IssueIdentity(node["id"], node["node_id"]),
            title=node["title"],
            body=node.get("body") or "",
            url=node["html_url"],
            state=PullRequestState(node["state"]),
            draft=node.get("draft", False),
            merged=node.get("merged", False),
            base_ref=node["base"]["ref"],
            head_ref=head["ref"],
            head_sha=head["sha"],
            head_repository=head_repository,
            author=_login(node.get("user")),
            mergeability=Mergeability.of(node.get("mergeable")),
            merge_state=MergeState(node.get("mergeable_state", "unknown")),
            requested_reviewers=tuple(reviewer["login"] for reviewer in node.get("requested_reviewers", ())),
            payload=node,
        )

    def _thread(self, node: dict[str, Any]) -> ReviewThread:
        comments = node["comments"]
        nodes = list(comments["nodes"])
        while comments["pageInfo"]["hasNextPage"]:
            response = self._repo.json("api", "graphql", "-f", f"query={_COMMENTS}", "-f", f"id={node['id']}",
                                       "-f", f"cursor={comments['pageInfo']['endCursor']}")
            comments = response["data"]["node"]["comments"]
            nodes.extend(comments["nodes"])
        submitted = (_review_comment(comment) for comment in nodes)
        return ReviewThread(
            id=node["id"],
            resolved=node["isResolved"],
            outdated=node["isOutdated"],
            path=node["path"],
            line=node["line"],
            original_line=node["originalLine"],
            # A comment of a review its author has not submitted is private to them.
            comments=tuple(comment for comment in submitted if comment.review_state is not ReviewVerdict.PENDING),
        )

    def _comments(self, pull: IssueRef) -> tuple[Comment, ...]:
        nodes = self._repo.pages(self._repo.endpoint(f"issues/{pull.number}/comments?{_PER_PAGE}"))
        return tuple(comment_from_rest(pull, node) for node in nodes)

    def _reviews(self, number: int) -> tuple[PullRequestReview, ...]:
        nodes = self._repo.pages(self._repo.endpoint(f"pulls/{number}/reviews?{_PER_PAGE}"))
        return tuple(
            PullRequestReview(
                id=node["id"],
                reviewer=_login(node.get("user")),
                verdict=ReviewVerdict(node["state"]),
                commit=node.get("commit_id") or "",
                body=node.get("body") or "",
                submitted_at=node.get("submitted_at"),
                payload=node,
            )
            for node in nodes
        )

    def _previous_head(self, number: int, head_sha: str) -> str | None:
        """The commit of the pull request just before its head, or None when the head is its first commit or is not in the listing."""
        shas = [node["sha"] for node in self._repo.pages(self._repo.endpoint(f"pulls/{number}/commits?{_PER_PAGE}"))]
        return shas[shas.index(head_sha) - 1] if head_sha in shas[1:] else None

    def _check_runs(self, sha: str) -> list[CheckResult]:
        nodes = self._repo.pages(self._repo.endpoint(f"commits/{sha}/check-runs?{_PER_PAGE}"), items="check_runs")
        return [
            CheckResult(CheckKind.CHECK_RUN, node["id"], node["name"], node.get("head_sha", sha),
                        _outcome(node["status"], node.get("conclusion")), payload=node)
            for node in nodes
        ]

    def _statuses(self, sha: str) -> list[CheckResult]:
        nodes = self._repo.pages(self._repo.endpoint(f"commits/{sha}/statuses?{_PER_PAGE}"))
        return [
            CheckResult(CheckKind.STATUS, node["id"], node["context"], sha,
                        _STATUS_OUTCOMES.get(node["state"], CheckOutcome.FAILED), payload=node)
            for node in nodes
        ]

    def _workflow_runs(self, sha: str) -> list[CheckResult]:
        nodes = self._repo.pages(self._repo.endpoint(f"actions/runs?head_sha={sha}&{_PER_PAGE}"), items="workflow_runs")
        return [
            CheckResult(CheckKind.WORKFLOW_RUN, node["id"], node.get("name", ""), node["head_sha"],
                        _outcome(node["status"], node.get("conclusion")), payload=node)
            for node in nodes
        ]


def _login(user: dict[str, Any] | None) -> str:
    # `user` is null for deleted accounts.
    return (user or {}).get("login", "ghost")


def _outcome(status: str, conclusion: str | None) -> CheckOutcome:
    if status != "completed":
        return CheckOutcome.PENDING
    return CheckOutcome.PASSED if conclusion in _PASSED else CheckOutcome.FAILED


def _review_comment(node: dict[str, Any]) -> ReviewComment:
    review, author = node["pullRequestReview"], node["author"]
    return ReviewComment(
        id=node["id"],
        database_id=node["databaseId"],
        author=author["login"] if author else None,
        author_association=node["authorAssociation"],
        body=node["body"],
        created_at=node["createdAt"],
        diff_hunk=node["diffHunk"],
        url=node["url"],
        review_state=ReviewVerdict(review["state"]) if review else None,
    )

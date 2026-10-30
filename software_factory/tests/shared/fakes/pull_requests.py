"""The pull request side of the shared `gh` fake: stored pull requests served in GitHub's REST and GraphQL shapes, with their writes applied."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from typing import Any

from software_factory.shared.process import CommandError


def rest_user(login: str) -> dict:
    """A REST user object, with the extra fields GitHub always sends."""
    return {"login": login, "id": 4000 + len(login), "node_id": f"U_{login}", "type": "User", "site_admin": False}


def comment_node(
    id: str = "c1",
    login: str | None = "reviewer",
    body: str = "Check this",
    *,
    association: str = "COLLABORATOR",
    review_state: str | None = "COMMENTED",
) -> dict:
    """A review comment in the GraphQL shape the thread queries select."""
    return {
        "id": id,
        "databaseId": 7000 + len(id),
        "body": body,
        "createdAt": "2026-03-01T10:00:00Z",
        "diffHunk": "@@ -1 +1 @@\n-old\n+new",
        "author": None if login is None else {"login": login},
        "authorAssociation": association,
        "pullRequestReview": None if review_state is None else {"state": review_state},
        "url": f"https://github.com/a/b/pull/1#discussion_{id}",
    }


def thread_node(
    comments: list[dict] | None = None,
    *,
    id: str = "t1",
    resolved: bool = False,
    outdated: bool = False,
    path: str = "cart.py",
    line: int | None = 4,
    original_line: int | None = 4,
) -> dict:
    """A review thread in the GraphQL shape the thread query selects."""
    return {
        "id": id,
        "isResolved": resolved,
        "isOutdated": outdated,
        "path": path,
        "line": line,
        "originalLine": original_line,
        "comments": comments if comments is not None else [comment_node()],
    }


def issue_comment(id: int, login: str, body: str, *, association: str = "COLLABORATOR") -> dict:
    """A conversation comment in the REST shape."""
    return {
        "id": id,
        "node_id": f"IC_{id}",
        "html_url": f"https://github.com/a/b/pull/1#issuecomment-{id}",
        "user": rest_user(login),
        "author_association": association,
        "body": body,
        "created_at": "2026-03-01T11:00:00Z",
        "updated_at": "2026-03-01T11:05:00Z",
    }


def review(
    id: int,
    login: str,
    body: str,
    *,
    state: str = "COMMENTED",
    commit: str = "sha0",
    association: str = "COLLABORATOR",
) -> dict:
    """A pull request review in the REST shape."""
    return {
        "id": id,
        "node_id": f"PRR_{id}",
        "html_url": f"https://github.com/a/b/pull/1#pullrequestreview-{id}",
        "user": rest_user(login),
        "author_association": association,
        "body": body,
        "state": state,
        "commit_id": commit,
        "submitted_at": "2026-03-01T12:00:00Z",
    }


def check_run(id: int, name: str, *, status: str = "completed", conclusion: str | None = "success", commit: str | None = None) -> dict:
    """A check run in the REST shape; `commit` is left out the way an older payload may leave it."""
    run = {
        "id": id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "app": {"id": 15368, "slug": "github-actions"},
        "html_url": f"https://github.com/a/b/runs/{id}",
        "details_url": f"https://github.com/a/b/actions/runs/{id}/job/{id}",
    }
    if commit is not None:
        run["head_sha"] = commit
    return run


def commit_status(id: int, context: str, *, state: str = "success", commit: str | None = None) -> dict:
    """A commit status in the REST shape; `commit` (default: the head commit) is the commit it was posted on, which the REST payload itself does not carry."""
    status = {"id": id, "context": context, "state": state, "target_url": f"https://ci.example/{id}"}
    if commit is not None:
        status["_commit"] = commit
    return status


def workflow_run(
    id: int,
    commit: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    workflow_id: int = 1,
    attempt: int = 1,
    event: str = "pull_request",
) -> dict:
    """A workflow run in the REST shape."""
    return {
        "id": id,
        "workflow_id": workflow_id,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "run_attempt": attempt,
        "head_sha": commit,
        "html_url": f"https://github.com/a/b/actions/runs/{id}",
    }


def pull_request_rule(
    approvals: int = 1, *, code_owners: bool = False, dismiss_stale: bool = False, threads: bool = False
) -> dict:
    """A repository ruleset's pull request rule in the REST shape of the rules-for-branch listing."""
    parameters = {
        "required_approving_review_count": approvals,
        "require_code_owner_review": code_owners,
        "dismiss_stale_reviews_on_push": dismiss_stale,
        "required_review_thread_resolution": threads,
        "require_last_push_approval": False,
    }
    return {"type": "pull_request", "parameters": parameters, "ruleset_source_type": "Repository", "ruleset_source": "a/b", "ruleset_id": 1}


def required_checks_rule(*contexts: str, strict: bool = False) -> dict:
    """A repository ruleset's required status checks rule in the REST shape of the rules-for-branch listing."""
    parameters = {
        "required_status_checks": [{"context": context, "integration_id": 15368} for context in contexts],
        "strict_required_status_checks_policy": strict,
    }
    return {"type": "required_status_checks", "parameters": parameters, "ruleset_source_type": "Repository", "ruleset_source": "a/b", "ruleset_id": 2}


def classic_protection(
    approvals: int | None = None,
    *,
    checks: tuple[str, ...] = (),
    strict: bool = False,
    code_owners: bool = False,
    dismiss_stale: bool = False,
    linear_history: bool = False,
    conversation_resolution: bool = False,
) -> dict:
    """A branch's classic protection in the REST shape; a requirement left out is not enabled."""
    protection: dict = {
        "url": "https://api.github.com/repos/a/b/branches/main/protection",
        "required_linear_history": {"enabled": linear_history},
        "required_conversation_resolution": {"enabled": conversation_resolution},
        "enforce_admins": {"enabled": False},
    }
    if approvals is not None:
        protection["required_pull_request_reviews"] = {
            "required_approving_review_count": approvals,
            "require_code_owner_reviews": code_owners,
            "dismiss_stale_reviews": dismiss_stale,
        }
    if checks or strict:
        protection["required_status_checks"] = {
            "strict": strict,
            "contexts": list(checks),
            "checks": [{"context": context, "app_id": 15368} for context in checks],
        }
    return protection


def _items(stored: list[dict] | Callable[[], list[dict]]) -> list[dict]:
    return stored() if callable(stored) else stored


class FakePull:
    """One stored pull request with everything GitHub keeps beside it; tests mutate it between reads, and a result list may be a callable evaluated on every read."""

    def __init__(self, number: int, *, head_ref: str = "feature", head_sha: str | Callable[[], str] = "sha0", author: str = "me") -> None:
        self.number = number
        self.author = author
        self.title = f"Change {number}"
        self.body = "Body"
        self.base_ref = "main"
        self.head_ref = head_ref
        self.head_sha = head_sha
        # The commits of the pull request before its head, oldest first.
        self.earlier_commits: list[str] = []
        self.head_repo = "a/b"
        self.state = "open"
        self.merged = False
        self.draft = False
        self.mergeable: bool | None = True
        self.mergeable_state = "clean"
        self.requested_reviewers: list[str] = []
        self.review_decision = ""
        self.merge_state_status = "CLEAN"
        self.threads: list[dict] = []
        self.comments: list[dict] = []
        self.reviews: list[dict] = []
        self.check_runs: list[dict] | Callable[[], list[dict]] = []
        self.statuses: list[dict] | Callable[[], list[dict]] = []
        self.workflow_runs: list[dict] | Callable[[], list[dict]] = []

    @property
    def sha(self) -> str:
        return self.head_sha() if callable(self.head_sha) else self.head_sha

    def close(self, *, merged: bool = False) -> None:
        """Closes the pull request, merged or not."""
        self.state, self.merged = "closed", merged

    def rest(self, *, listed: bool = False) -> dict:
        """The REST payload; a listing omits the fields GitHub computes only for a single read."""
        repository = {"full_name": self.head_repo, "id": 11, "private": False}
        payload: dict[str, Any] = {
            "url": f"https://api.github.com/repos/a/b/pulls/{self.number}",
            "id": 9000 + self.number,
            "node_id": f"PR_{self.number}",
            "html_url": f"https://github.com/a/b/pull/{self.number}",
            "number": self.number,
            "state": self.state,
            "locked": False,
            "title": self.title,
            "user": rest_user(self.author),
            "body": self.body,
            "created_at": "2026-02-28T09:00:00Z",
            "updated_at": "2026-03-01T09:00:00Z",
            "draft": self.draft,
            "head": {"label": f"a:{self.head_ref}", "ref": self.head_ref, "sha": self.sha, "repo": repository},
            "base": {"label": f"a:{self.base_ref}", "ref": self.base_ref, "sha": "base0", "repo": {"full_name": "a/b"}},
            "requested_reviewers": [rest_user(login) for login in self.requested_reviewers],
            "labels": [],
            "author_association": "OWNER",
        }
        if not listed:
            payload |= {"merged": self.merged, "mergeable": self.mergeable, "mergeable_state": self.mergeable_state}
        return payload


class FakePullRequestHost:
    """The pull requests of one repository, answering the `gh` calls that read and change them."""

    def __init__(self, *, slug: str = "a/b", login: str = "me", page_size: int = 100, thread_page_size: int = 100, comment_page_size: int = 100) -> None:
        self.slug = slug
        self.login = login
        self.page_size = page_size
        self.thread_page_size = thread_page_size
        self.comment_page_size = comment_page_size
        self.pulls: dict[int, FakePull] = {}
        self.logs = "cart.py: duplicate product"
        self.replies: list[str] = []
        self.retries: list[int] = []
        self.requested: list[int] = []
        self.reply_failure: str | None = None
        self.thread_failure: str | None = None
        self.rules: dict[str, list[dict]] = {}
        self.protection: dict[str, dict] = {}

    def add(self, number: int, **options: Any) -> FakePull:
        """Stores a new open pull request."""
        pull = FakePull(number, **options)
        self.pulls[number] = pull
        return pull

    def handle(self, args: tuple[str, ...]) -> str | None:
        """The stdout for `args` when they address a stored pull request, else None."""
        if args == ("api", "user"):
            return json.dumps({"login": self.login})
        if args[:2] == ("api", "graphql"):
            return self._graphql(args)
        if args[:2] == ("api", "--paginate") and "--slurp" in args:
            return self._collection(args[-1])
        if args[0] == "api" and args[1].startswith(f"repos/{self.slug}/"):
            return self._rest(args)
        return self._commands(args)

    def _rest(self, args: tuple[str, ...]) -> str | None:
        path = args[1].removeprefix(f"repos/{self.slug}/")
        if match := re.fullmatch(r"pulls/(\d+)", path):
            return json.dumps(self._pull(int(match[1])).rest())
        if (match := re.fullmatch(r"issues/(\d+)/comments", path)) and int(match[1]) in self.pulls:
            return self._post_comment(self._pull(int(match[1])), args)
        if match := re.fullmatch(r"branches/(.+)/protection", path):
            return self._protection(match[1])
        return None

    def _protection(self, branch: str) -> str:
        if branch not in self.protection:
            raise CommandError(f"gh api repos/{self.slug}/branches/{branch}/protection", 1, "gh: Branch not protected (HTTP 404)")
        return json.dumps(self.protection[branch])

    def _post_comment(self, pull: FakePull, args: tuple[str, ...]) -> str:
        body = next(arg[len("body="):] for arg in args if arg.startswith("body="))
        comment = issue_comment(500 + len(pull.comments), self.login, body)
        pull.comments.append(comment)
        self.replies.append(body)
        return json.dumps(comment)

    def _collection(self, endpoint: str) -> str | None:
        path, _, query = endpoint.removeprefix(f"repos/{self.slug}/").partition("?")
        if path == "pulls":
            return self._paged([pull.rest(listed=True) for pull in self.pulls.values() if pull.state == "open"])
        if (match := re.fullmatch(r"(issues|pulls)/(\d+)/(comments|reviews)", path)) and int(match[2]) in self.pulls:
            pull = self._pull(int(match[2]))
            return self._paged(pull.comments if match[3] == "comments" else pull.reviews)
        if (match := re.fullmatch(r"pulls/(\d+)/commits", path)) and int(match[1]) in self.pulls:
            pull = self._pull(int(match[1]))
            return self._paged([{"sha": sha} for sha in (*pull.earlier_commits, pull.sha)])
        if match := re.fullmatch(r"commits/([^/]+)/(check-runs|statuses)", path):
            pull, sha = self._pull_at(match[1]), match[1]
            if match[2] == "check-runs":
                return self._wrapped("check_runs", [run for run in _items(pull.check_runs) if run.get("head_sha", pull.sha) == sha])
            return self._paged([
                {key: value for key, value in status.items() if key != "_commit"}
                for status in _items(pull.statuses)
                if status.get("_commit", pull.sha) == sha
            ])
        if path.startswith("rules/branches/"):
            return self._paged(self.rules.get(path.removeprefix("rules/branches/"), []))
        if path == "actions/runs":
            sha = query.split("head_sha=")[1].split("&")[0]
            return self._wrapped("workflow_runs", [run for run in _items(self._pull_at(sha).workflow_runs) if run["head_sha"] == sha])
        return None

    def _commands(self, args: tuple[str, ...]) -> str | None:
        if args[:2] == ("pr", "view") and int(args[2]) in self.pulls:
            pull = self._pull(int(args[2]))
            return json.dumps({"mergeStateStatus": pull.merge_state_status, "reviewDecision": pull.review_decision})
        if args[:2] == ("run", "view"):
            return self.logs
        if args[:2] == ("run", "rerun"):
            self.retries.append(int(args[2]))
            return ""
        if args[:2] == ("pr", "edit") and "--add-reviewer" in args and int(args[2]) in self.pulls:
            self.requested.append(int(args[2]))
            self._pull(int(args[2])).requested_reviewers.append("copilot-pull-request-reviewer[bot]")
            return ""
        if args[:2] == ("pr", "edit") and "--body" in args and int(args[2]) in self.pulls:
            self._pull(int(args[2])).body = args[args.index("--body") + 1]
            return ""
        if args[:2] in (("pr", "close"), ("pr", "reopen")) and int(args[2]) in self.pulls:
            self._pull(int(args[2])).state = "closed" if args[1] == "close" else "open"
            return ""
        return None

    def _graphql(self, args: tuple[str, ...]) -> str:
        fields = {arg.split("=", 1)[0]: arg.split("=", 1)[1] for arg in args[2:] if "=" in arg and not arg.startswith("-")}
        query = fields["query"]
        if "addPullRequestReviewThreadReply" in query:
            return self._reply(fields["id"], fields["body"])
        if "resolveReviewThread" in query:
            return self._set_resolved(fields["id"], resolved="unresolveReviewThread" not in query)
        if "reviewThreads" in query:
            return self._threads(self._pull(int(fields["number"])), fields.get("cursor"))
        thread = self._thread(fields["id"])
        return json.dumps({"data": {"node": {"comments": self._comment_page(thread, int(fields["cursor"]))}}})

    def _threads(self, pull: FakePull, cursor: str | None) -> str:
        start = int(cursor or 0)
        end = start + self.thread_page_size
        nodes = [{**thread, "comments": self._comment_page(thread, 0)} for thread in pull.threads[start:end]]
        connection = {"pageInfo": {"hasNextPage": end < len(pull.threads), "endCursor": str(end)}, "nodes": nodes}
        return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": connection}}}})

    def _comment_page(self, thread: dict, start: int) -> dict:
        end = start + self.comment_page_size
        page = {"hasNextPage": end < len(thread["comments"]), "endCursor": str(end)}
        return {"pageInfo": page, "nodes": copy.deepcopy(thread["comments"][start:end])}

    def _reply(self, thread_id: str, body: str) -> str:
        if self.reply_failure:
            return json.dumps({"errors": [{"message": self.reply_failure}]})
        thread = self._thread(thread_id)
        comment = comment_node(f"reply{len(self.replies) + 1}", self.login, body)
        thread["comments"].append(comment)
        self.replies.append(body)
        return json.dumps({"data": {"addPullRequestReviewThreadReply": {"comment": {"id": comment["id"]}}}})

    def _set_resolved(self, thread_id: str, *, resolved: bool) -> str:
        if self.thread_failure:
            return json.dumps({"errors": [{"message": self.thread_failure}]})
        thread = self._thread(thread_id)
        thread["isResolved"] = resolved
        return json.dumps({"data": {"resolveReviewThread": {"thread": {"id": thread_id, "isResolved": resolved}}}})

    def _thread(self, thread_id: str) -> dict:
        for pull in self.pulls.values():
            for thread in pull.threads:
                if thread["id"] == thread_id:
                    return thread
        raise CommandError("gh api graphql", 1, f"no thread {thread_id}")

    def _pull(self, number: int) -> FakePull:
        if number not in self.pulls:
            raise CommandError(f"gh api repos/{self.slug}/pulls/{number}", 1, "gh: Not Found (HTTP 404)")
        return self.pulls[number]

    def _pull_at(self, sha: str) -> FakePull:
        for pull in self.pulls.values():
            if sha in (pull.sha, *pull.earlier_commits):
                return pull
        raise CommandError(f"gh api commits/{sha}", 1, "gh: Not Found (HTTP 404)")

    def _paged(self, items: list[dict]) -> str:
        size = self.page_size
        return json.dumps([items[start : start + size] for start in range(0, len(items), size)] or [[]])

    def _wrapped(self, key: str, items: list[dict]) -> str:
        """Pages of `{key: [...]}` objects, as the check-run and workflow-run listings answer."""
        size = self.page_size
        pages = [items[start : start + size] for start in range(0, len(items), size)] or [[]]
        return json.dumps([{"total_count": len(items), key: page} for page in pages])

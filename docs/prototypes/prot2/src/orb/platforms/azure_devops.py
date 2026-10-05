from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path

from orb.contracts.platform_adapter import PlatformAdapter, PullRequest, ReviewThread, WorkItem

AzRunner = Callable[[tuple[str, ...]], str]

_OPEN_STATES = {"new", "active", "committed"}
_RESOLVED_THREAD_STATUSES = {"fixed", "closed", "wontfix", "bydesign"}
_SPECS_WIQL = (
    "SELECT [System.Id] FROM WorkItems "
    "WHERE [System.Tags] CONTAINS 'spec' AND [System.State] <> 'Closed'"
)


class AzureDevOpsAdapter(PlatformAdapter):
    """Translates the platform-neutral contract to `az`; no Azure DevOps shape leaves this class."""

    def __init__(self, org: str, project: str, repo: str, *, az: AzRunner, dry_run: bool = False) -> None:
        self._org = org
        self._project = project
        self._repo = repo
        self._az = az
        self._dry_run = dry_run

    def list_specs(self) -> list[WorkItem]:
        output = self._az(("boards", "query", "--wiql", _SPECS_WIQL, "--output", "json"))
        return [
            WorkItem(
                id=str(item["id"]),
                title=item["fields"]["System.Title"],
                state="open" if item["fields"]["System.State"].lower() in _OPEN_STATES else "closed",
                tags=tuple(tag.strip() for tag in item["fields"]["System.Tags"].split(";") if tag.strip()),
                url=item["url"],
            )
            for item in json.loads(output)
        ]

    def list_pull_requests(self) -> list[PullRequest]:
        output = self._az(
            ("repos", "pr", "list", "--repository", self._repo, "--status", "active", "--output", "json")
        )
        return [
            PullRequest(
                id=str(pr["pullRequestId"]),
                title=pr["title"],
                url=pr["url"],
                branch=pr["sourceRefName"].removeprefix("refs/heads/"),
            )
            for pr in json.loads(output)
        ]

    def review_threads(self, pull_request_id: str) -> list[ReviewThread]:
        output = self._az(self._threads_args("pullRequestThreads", pull_request_id))
        return [
            ReviewThread(
                id=str(thread["id"]),
                path=thread["threadContext"]["filePath"].lstrip("/"),
                body="\n".join(comment["content"] for comment in thread["comments"]),
                resolved=thread["status"].lower() in _RESOLVED_THREAD_STATUSES,
            )
            for thread in json.loads(output)["value"]
            if thread.get("threadContext")
        ]

    def reply_to_thread(self, pull_request_id: str, thread_id: str, body: str) -> None:
        if self._dry_run:
            return
        with tempfile.TemporaryDirectory() as directory:
            body_file = Path(directory) / "comment.json"
            body_file.write_text(json.dumps({"content": body, "commentType": 1}))
            self._az(
                self._threads_args(
                    "pullRequestThreadComments",
                    pull_request_id,
                    route=(f"threadId={thread_id}",),
                    extra=("--http-method", "POST", "--in-file", str(body_file)),
                )
            )

    def _threads_args(
        self,
        resource: str,
        pull_request_id: str,
        *,
        route: tuple[str, ...] = (),
        extra: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        return (
            "devops", "invoke", "--area", "git", "--resource", resource,
            "--organization", f"https://dev.azure.com/{self._org}",
            "--route-parameters",
            f"project={self._project}", f"repositoryId={self._repo}", f"pullRequestId={pull_request_id}", *route,
            *extra,
            "--output", "json",
        )

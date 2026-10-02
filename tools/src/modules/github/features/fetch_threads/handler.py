"""Fetch Threads use case: fetches actionable review threads for a single PR URL."""

from ...domain.services.thread_filter import ThreadFilter
from ...domain.review_thread import ReviewThread
from ...infrastructure.vcs_client import VCSClient
from ...shared.pr_url import parse_pr_url


def fetch_threads(
    pr_url: str,
    *,
    vcs: VCSClient | None = None,
) -> list[dict]:
    """Fetch and return actionable review threads for a PR URL as serialisable dicts.

    The returned shape matches the format consumed by the Copilot fix skill:
        {
            "thread_id": str,
            "prefix": str,
            "path": str,
            "lines": str,
            "actionable_comment": str,
            "comments": [{"author": str, "body": str}],
        }

    Args:
        pr_url: Full GitHub PR URL (e.g. https://github.com/owner/repo/pull/123).
        vcs:    VCSClient instance (defaults to VCSClient()).
    """
    owner, repo_name, number = parse_pr_url(pr_url)
    vcs = vcs or VCSClient()
    thread_filter = ThreadFilter()

    fetched_threads = vcs.fetch_review_threads(owner, repo_name, number)
    actionable_threads = thread_filter.get_actionable_threads(fetched_threads)

    return [_thread_to_dict(t) for t in actionable_threads]


def _thread_to_dict(t: ReviewThread) -> dict:
    return {
        "thread_id": t.thread_id,
        "prefix": next(
            (lbl.value for c in reversed(t.comments) if (lbl := c.get_label()) is not None),
            "",
        ),
        "path": t.path,
        "lines": t.lines,
        "actionable_comment": t.actionable_comment,
        "comments": [{"author": c.author, "body": c.body} for c in t.comments],
    }

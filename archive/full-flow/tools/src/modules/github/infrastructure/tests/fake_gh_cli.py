"""Test double for GhCli — returns configured data without spawning subprocesses."""

from ..gh_cli import GhCli


class FakeGhCli(GhCli):
    """In-memory GhCli substitute for use in tests.

    Usage::

        gh = FakeGhCli(
            pr_list_output="https://github.com/owner/repo/pull/1\\n",
            threads_raw=[{"id": "T1", "isResolved": False, ...}],
        )
        client = VCSClient(gh=gh)
    """

    def __init__(
        self,
        pr_list_output: list[dict] | None = None,
        threads_raw: list[dict] | None = None,
        issues_raw: list[dict] | None = None,
        specs_raw: list[dict] | None = None,
        checkout_error: Exception | None = None,
    ) -> None:
        self._pr_list_output = pr_list_output or []
        self._threads_raw = threads_raw or []
        self._issues_raw = issues_raw or []
        self._specs_raw = specs_raw or []
        self._checkout_error = checkout_error

    def pr_list(self, user: str, repo: str) -> list[dict]:
        return self._pr_list_output

    def fetch_threads_raw(self, owner: str, repo: str, number: int) -> list[dict]:
        return self._threads_raw

    def fetch_issues_raw(self, owner: str, repo: str, spec_number: int | None = None) -> list[dict]:
        return self._issues_raw

    def list_specs_raw(self, owner: str, repo: str) -> list[dict]:
        return self._specs_raw

    def pr_checkout(self, pr_url: str) -> None:
        if self._checkout_error:
            raise self._checkout_error

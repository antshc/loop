"""In-memory GitHub and a scripted agent for the demo and tests."""

from __future__ import annotations

from collections.abc import Iterable

from .context import AgentResult
from .integrations import CheckResult, Issue


class FakeGitHubClient:
    """Serves issues from memory."""

    def __init__(self, issues: Iterable[Issue]) -> None:
        self._issues = {(issue.repo, issue.number): issue for issue in issues}
        self.fetches = 0

    async def get_issue(self, repo: str, number: int) -> Issue:
        """The stored issue; an unknown one raises `LookupError`."""
        self.fetches += 1
        try:
            return self._issues[(repo, number)]
        except KeyError as exception:
            raise LookupError(f"no issue {repo}#{number}") from exception


class ScriptedChecks:
    """Returns the scripted check results in order, then passes."""

    def __init__(self, *results: CheckResult) -> None:
        self._results = list(results)
        self.runs = 0

    async def run(self) -> CheckResult:
        """The next scripted result, or a pass once they run out."""
        self.runs += 1
        return self._results.pop(0) if self._results else CheckResult(True, "")


class ScriptedAgentClient:
    """Returns the scripted results in order and records every prompt."""

    def __init__(self, *results: AgentResult) -> None:
        self._results = list(results)
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> AgentResult:
        """The next scripted result."""
        self.prompts.append(prompt)
        if not self._results:
            raise AssertionError("scripted agent has no result left")
        return self._results.pop(0)

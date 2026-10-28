"""Reads the merge policy of a base branch from repository rulesets and classic branch protection through `gh`."""

from __future__ import annotations

import logging
from urllib.parse import quote

from ...process import CommandError
from ..repo import GitHubRepo
from .merge_policy import (
    MergePolicySnapshot,
    PolicySource,
    policy_from_protection,
    policy_from_rules,
    unavailable_policy,
)


logger = logging.getLogger("workflow.pull_requests")


# gh reports the HTTP status of a refused request in its error output.
_DENIED = ("HTTP 403", "HTTP 404")

# GitHub answers 404 for a branch without classic protection; that is no policy, not a refused read.
_UNPROTECTED = "Branch not protected"


class MergePolicyReader:
    """Reads the merge policy of one repository's branches; a source GitHub denies is reported unavailable, never as no policy."""

    def __init__(self, repo: GitHubRepo) -> None:
        self._repo = repo

    def policy(self, base_ref: str) -> MergePolicySnapshot:
        """The combined policy of the rulesets and the classic protection of `base_ref`, the stricter requirement wherever both apply."""
        return self._rulesets(base_ref).stricter(self._protection(base_ref))

    def _rulesets(self, base_ref: str) -> MergePolicySnapshot:
        try:
            return policy_from_rules(base_ref, self._repo.pages(self._endpoint("rules/branches", base_ref)))
        except CommandError as exception:
            return self._denied(base_ref, PolicySource.RULESETS, exception)

    def _protection(self, base_ref: str) -> MergePolicySnapshot:
        try:
            return policy_from_protection(base_ref, self._repo.json("api", self._endpoint("branches", base_ref, "protection")))
        except CommandError as exception:
            if _UNPROTECTED in exception.output:
                return MergePolicySnapshot(base_ref)
            return self._denied(base_ref, PolicySource.BRANCH_PROTECTION, exception)

    @staticmethod
    def _denied(base_ref: str, source: PolicySource, exception: CommandError) -> MergePolicySnapshot:
        if not any(status in exception.output for status in _DENIED):
            raise exception
        logger.warning("merge policy of %s unavailable from %s: %s", base_ref, source, exception.output.strip())
        return unavailable_policy(base_ref, source)

    def _endpoint(self, collection: str, base_ref: str, *rest: str) -> str:
        return "/".join((f"repos/{self._repo.slug}", collection, quote(base_ref, safe="/"), *rest))

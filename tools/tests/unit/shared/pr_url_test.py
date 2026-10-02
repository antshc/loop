#!/usr/bin/env python3
"""Unit tests for parse_pr_url().

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from modules.github.shared.pr_url import parse_pr_url


class TestPRURLParsing:
    """Feature: PR URL Parsing"""

    def test_valid_pr_url_is_parsed_correctly(self):
        # Scenario: Valid PR URL is parsed correctly
        assert parse_pr_url("https://github.com/owner/repo/pull/123") == ("owner", "repo", 123)

    def test_pr_url_with_numeric_owner_repo(self):
        # Scenario: PR URL with numeric owner/repo
        assert parse_pr_url("https://github.com/user42/my-repo/pull/7") == ("user42", "my-repo", 7)

#!/usr/bin/env python3
"""Unit tests for the fix_prs CLI entry point.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

import argparse
from datetime import date
from pathlib import Path

import pytest

import afk.features.fix_prs.cli as fix_prs_cli


class TestFixPrsCli:
    """Feature: Fix PRs CLI"""

    def test_parser_applies_default_arguments(self):
        # Scenario: Parser applies default arguments
        args = fix_prs_cli._build_parser().parse_args([])

        assert args.github_user is None
        assert args.github_repo is None
        assert args.max_executions == fix_prs_cli.DEFAULT_MAX_EXECUTIONS
        assert args.agent == "yolo"
        assert args.prompt == "/ralph:fix"
        assert args.log_dir == Path("/var/log/ralph")

    def test_parser_accepts_custom_arguments(self):
        # Scenario: Parser accepts custom arguments
        args = fix_prs_cli._build_parser().parse_args([
            "--github_user", "alice",
            "--github_repo", "owner/repo",
            "--max_executions", "3",
            "--agent", "other-agent",
            "--prompt", "/custom:fix",
            "--log-dir", "custom-logs",
        ])

        assert args.github_user == "alice"
        assert args.github_repo == "owner/repo"
        assert args.max_executions == 3
        assert args.agent == "other-agent"
        assert args.prompt == "/custom:fix"
        assert args.log_dir == Path("custom-logs")

    def test_github_repo_validator_rejects_invalid_repository_format(self):
        # Scenario: GitHub repo validator rejects invalid repository format
        with pytest.raises(argparse.ArgumentTypeError, match="Expected owner/repo"):
            fix_prs_cli._github_repo("owner-repo")

    def test_main_calls_configure_logging_with_dated_log_file(self, monkeypatch):
        # Scenario: Main calls configure_logging with a dated log file path
        args = argparse.Namespace(
            github_user="alice",
            github_repo="owner/repo",
            max_executions=5,
            agent="yolo",
            prompt="/ralph:fix",
            log_dir=Path("fix-logs"),
        )
        captured_configure_logging = {}
        captured_fix_prs_call = {}

        class FakeParser:
            def parse_args(self):
                return args

        monkeypatch.setattr(fix_prs_cli, "_build_parser", lambda: FakeParser())
        monkeypatch.setattr(
            fix_prs_cli,
            "configure_logging",
            lambda log_file: captured_configure_logging.update(log_file=log_file),
        )
        monkeypatch.setattr(
            fix_prs_cli,
            "fix_prs",
            lambda github_user, github_repo, log_dir, max_executions, prompt, agent_name: captured_fix_prs_call.update(
                github_user=github_user,
                github_repo=github_repo,
                log_dir=log_dir,
                max_executions=max_executions,
                prompt=prompt,
                agent_name=agent_name,
            ),
        )

        result = fix_prs_cli.main()

        assert result is None
        assert captured_configure_logging["log_file"] == args.log_dir / f"fix_prs-{date.today()}.log"
        assert captured_fix_prs_call == {
            "github_user": "alice",
            "github_repo": "owner/repo",
            "log_dir": args.log_dir,
            "max_executions": 5,
            "prompt": "/ralph:fix",
            "agent_name": "yolo",
        }

    def test_main_does_not_use_afk_debug_env_var(self, monkeypatch):
        # Scenario: Main does not inspect AFK_DEBUG (level is delegated to configure_logging)
        args = argparse.Namespace(
            github_user=None,
            github_repo=None,
            max_executions=fix_prs_cli.DEFAULT_MAX_EXECUTIONS,
            agent="yolo",
            prompt="/ralph:fix",
            log_dir=Path("fix-logs"),
        )
        configure_logging_calls = []

        class FakeParser:
            def parse_args(self):
                return args

        monkeypatch.setattr(fix_prs_cli, "_build_parser", lambda: FakeParser())
        monkeypatch.setattr(
            fix_prs_cli,
            "configure_logging",
            lambda log_file: configure_logging_calls.append(log_file),
        )
        monkeypatch.setattr(fix_prs_cli, "fix_prs", lambda *a, **kw: None)
        monkeypatch.setenv("AFK_DEBUG", "1")

        fix_prs_cli.main()

        # configure_logging is called exactly once with no explicit level arg —
        # level resolution is fully delegated to configure_logging (reads AFK_LOG_LEVEL).
        assert len(configure_logging_calls) == 1

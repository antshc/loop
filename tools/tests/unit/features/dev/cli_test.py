#!/usr/bin/env python3
"""Unit tests for the dev CLI entry point.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

import argparse
from datetime import date
from pathlib import Path

import pytest

import afk.features.dev.cli as dev_cli


class TestDevCli:
    """Feature: Dev CLI"""

    def test_parser_applies_default_arguments_for_valid_repository(self):
        # Scenario: Parser applies default arguments for valid repository
        args = dev_cli._build_parser().parse_args(["--github_repo_board", "owner/repo"])

        assert args.github_repo_board == "owner/repo"
        assert args.max_executions == dev_cli.DEFAULT_MAX_EXECUTIONS
        assert args.agent == "yolo"
        assert args.prompt == "/ralph:dev"
        assert args.log_dir == Path("/var/log/ralph")

    def test_parser_accepts_custom_arguments(self):
        # Scenario: Parser accepts custom arguments
        args = dev_cli._build_parser().parse_args([
            "--github_repo_board", "owner/repo",
            "--max_executions", "7",
            "--agent", "other-agent",
            "--prompt", "/custom:dev",
            "--log-dir", "custom-logs",
        ])

        assert args.github_repo_board == "owner/repo"
        assert args.max_executions == 7
        assert args.agent == "other-agent"
        assert args.prompt == "/custom:dev"
        assert args.log_dir == Path("custom-logs")

    def test_parser_accepts_no_arguments(self):
        # Scenario: Parser accepts no arguments (github_repo_board is optional)
        args = dev_cli._build_parser().parse_args([])

        assert args.github_repo_board is None

    def test_github_repo_validator_rejects_invalid_repository_format(self):
        # Scenario: GitHub repo validator rejects invalid repository format
        with pytest.raises(argparse.ArgumentTypeError, match="Expected owner/repo"):
            dev_cli._github_repo("owner-repo")

    def test_repo_dir_validator_rejects_missing_directory(self):
        # Scenario: Repo dir validator rejects missing directory
        with pytest.raises(argparse.ArgumentTypeError, match="repo-dir does not exist"):
            dev_cli._repo_dir("__missing_repo_dir__")

    def test_main_calls_configure_logging_with_dated_log_file(self, monkeypatch):
        # Scenario: Main calls configure_logging with a dated log file path
        args = argparse.Namespace(
            github_repo_board="owner/repo",
            max_executions=7,
            agent="other-agent",
            prompt="/custom:dev",
            log_dir=Path("dev-logs"),
        )
        captured_configure_logging = {}
        captured_dev_call = {}

        class FakeParser:
            def parse_args(self):
                return args

        monkeypatch.setattr(dev_cli, "_build_parser", lambda: FakeParser())
        monkeypatch.setattr(
            dev_cli,
            "configure_logging",
            lambda log_file: captured_configure_logging.update(log_file=log_file),
        )
        monkeypatch.setattr(
            dev_cli,
            "dev",
            lambda github_repo, log_dir, max_executions, prompt, agent_name: captured_dev_call.update(
                github_repo=github_repo,
                log_dir=log_dir,
                max_executions=max_executions,
                prompt=prompt,
                agent_name=agent_name,
            ),
        )

        result = dev_cli.main()

        assert result is None
        assert captured_configure_logging["log_file"] == args.log_dir / f"dev-{date.today()}.log"
        assert captured_dev_call == {
            "github_repo": "owner/repo",
            "log_dir": args.log_dir,
            "max_executions": 7,
            "prompt": "/custom:dev",
            "agent_name": "other-agent",
        }

    def test_main_does_not_use_afk_debug_env_var(self, monkeypatch):
        # Scenario: Main does not inspect AFK_DEBUG (level is delegated to configure_logging)
        args = argparse.Namespace(
            github_repo_board="owner/repo",
            max_executions=dev_cli.DEFAULT_MAX_EXECUTIONS,
            agent="yolo",
            prompt="/ralph:dev",
            log_dir=Path("dev-logs"),
        )
        configure_logging_calls = []

        class FakeParser:
            def parse_args(self):
                return args

        monkeypatch.setattr(dev_cli, "_build_parser", lambda: FakeParser())
        monkeypatch.setattr(
            dev_cli,
            "configure_logging",
            lambda log_file: configure_logging_calls.append(log_file),
        )
        monkeypatch.setattr(dev_cli, "dev", lambda *a, **kw: None)
        monkeypatch.setenv("AFK_DEBUG", "1")

        dev_cli.main()

        # configure_logging is called exactly once with no explicit level arg —
        # level resolution is fully delegated to configure_logging (reads AFK_LOG_LEVEL).
        assert len(configure_logging_calls) == 1

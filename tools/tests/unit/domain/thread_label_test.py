#!/usr/bin/env python3
"""Unit tests for ThreadLabel.is_actionable().

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from modules.github.domain.thread_label import ThreadLabel


class TestThreadLabelActionability:
    """Feature: ThreadLabel Actionability"""

    def test_fix_label_is_actionable(self):
        # Scenario: FIX label is actionable
        assert ThreadLabel.FIX.is_actionable() is True

    def test_suggest_bang_label_is_actionable(self):
        # Scenario: SUGGEST_BANG label is actionable
        assert ThreadLabel.SUGGEST_BANG.is_actionable() is True

    def test_suggest_label_is_not_actionable(self):
        # Scenario: SUGGEST label is NOT actionable
        assert ThreadLabel.SUGGEST.is_actionable() is False

    def test_nit_label_is_not_actionable(self):
        # Scenario: NIT label is NOT actionable
        assert ThreadLabel.NIT.is_actionable() is False

    def test_good_label_is_not_actionable(self):
        # Scenario: GOOD label is NOT actionable
        assert ThreadLabel.GOOD.is_actionable() is False

    def test_question_label_is_not_actionable(self):
        # Scenario: QUESTION label is NOT actionable
        assert ThreadLabel.QUESTION.is_actionable() is False

    def test_fixed_label_is_not_actionable(self):
        # Scenario: FIXED label is NOT actionable
        assert ThreadLabel.FIXED.is_actionable() is False

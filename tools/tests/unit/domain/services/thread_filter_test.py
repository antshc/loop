#!/usr/bin/env python3
"""Unit tests for domain comment, thread, and thread-filter logic.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

from modules.github.domain.comment import Comment
from modules.github.domain.review_thread import ReviewThread
from modules.github.domain.thread_label import ThreadLabel
from modules.github.domain.services.thread_filter import ThreadFilter


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_thread(id, path, start_line, end_line, body, resolved=False):
    return {
        "id": id,
        "isResolved": resolved,
        "path": path,
        "startLine": start_line,
        "line": end_line,
        "comments": [{"author": {"login": "reviewer"}, "body": body}],
    }


def make_thread_multi_comments(id, path, line, *bodies):
    return {
        "id": id,
        "isResolved": False,
        "path": path,
        "startLine": line,
        "line": line,
        "comments": [{"author": {"login": "reviewer"}, "body": b} for b in bodies],
    }


def build_thread(raw: dict) -> ReviewThread:
    """Build a ReviewThread domain object from a raw dict."""
    raw_comments = raw.get("comments", [])
    comments = [Comment(author=c["author"]["login"], body=c["body"]) for c in raw_comments]
    start = raw.get("startLine") or raw.get("line")
    end = raw.get("line")
    return ReviewThread(
        thread_id=raw["id"],
        path=raw.get("path", ""),
        lines=f"{start}-{end}",
        is_resolved=raw.get("isResolved", False),
        comments=comments,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestCommentLabelDetection:
    """Feature: Comment Label Detection"""

    def test_comment_with_fix_prefix_is_labeled_fix(self):
        # Scenario: Comment with "fix!:" prefix is labeled FIX
        assert Comment(author="", body="fix!: broken null check").get_label() == ThreadLabel.FIX

    def test_comment_with_suggest_bang_prefix_is_labeled_suggest_bang(self):
        # Scenario: Comment with "suggest!:" prefix is labeled SUGGEST_BANG
        assert Comment(author="", body="suggest!: consider extracting").get_label() == ThreadLabel.SUGGEST_BANG

    def test_comment_with_suggest_prefix_is_labeled_suggest(self):
        # Scenario: Comment with "suggest:" prefix is labeled SUGGEST
        assert Comment(author="", body="suggest: could improve readability").get_label() == ThreadLabel.SUGGEST

    def test_comment_with_nit_prefix_is_labeled_nit(self):
        # Scenario: Comment with "nit:" prefix is labeled NIT
        assert Comment(author="", body="nit: minor style issue").get_label() == ThreadLabel.NIT

    def test_comment_with_good_prefix_is_labeled_good(self):
        # Scenario: Comment with "good:" prefix is labeled GOOD
        assert Comment(author="", body="good: nice approach").get_label() == ThreadLabel.GOOD

    def test_comment_with_question_prefix_is_labeled_question(self):
        # Scenario: Comment with "question!:" prefix is labeled QUESTION
        assert Comment(author="", body="question!: why is this needed?").get_label() == ThreadLabel.QUESTION

    def test_comment_containing_fixed_is_labeled_fixed(self):
        # Scenario: Comment containing "Fixed." is labeled FIXED
        assert Comment(author="", body="Fixed.").get_label() == ThreadLabel.FIXED

    def test_comment_with_no_recognized_prefix_returns_none(self):
        # Scenario: Comment with no recognized prefix returns None
        assert Comment(author="", body="looks fine to me").get_label() is None


class TestCommentExclusionDetection:
    """Feature: Comment Exclusion Detection"""

    def test_comment_labeled_question_is_excluded(self):
        # Scenario: Comment labeled QUESTION is excluded
        assert Comment(author="", body="question!: why is this needed?").is_excluded() is True

    def test_comment_labeled_fixed_is_excluded(self):
        # Scenario: Comment labeled FIXED is excluded
        assert Comment(author="", body="Fixed.").is_excluded() is True

    def test_comment_labeled_fix_is_not_excluded(self):
        # Scenario: Comment labeled FIX is not excluded
        assert Comment(author="", body="fix!: broken null check").is_excluded() is False

    def test_comment_with_no_label_is_not_excluded(self):
        # Scenario: Comment with no label is not excluded
        assert Comment(author="", body="random text").is_excluded() is False


class TestThreadActionability:
    """Feature: Thread Actionability"""

    def test_thread_with_fix_label_is_actionable(self):
        # Scenario: Thread with fix! label is actionable
        thread = build_thread(make_thread("T1", "src/foo.ts", 1, 1, "fix!: broken null check"))
        assert thread.is_actionable is True

    def test_thread_with_suggest_bang_label_is_actionable(self):
        # Scenario: Thread with suggest! label is actionable
        thread = build_thread(make_thread("T2", "src/foo.ts", 1, 1, "suggest!: extract method"))
        assert thread.is_actionable is True

    def test_thread_with_suggest_label_is_not_actionable(self):
        # Scenario: Thread with suggest label is NOT actionable
        thread = build_thread(make_thread("T3", "src/foo.ts", 1, 1, "suggest: could improve"))
        assert thread.is_actionable is False

    def test_thread_with_nit_label_is_not_actionable(self):
        # Scenario: Thread with nit label is NOT actionable
        thread = build_thread(make_thread("T4", "src/foo.ts", 1, 1, "nit: minor style"))
        assert thread.is_actionable is False

    def test_thread_with_good_label_is_not_actionable(self):
        # Scenario: Thread with good label is NOT actionable
        thread = build_thread(make_thread("T5", "src/foo.ts", 1, 1, "good: nice approach"))
        assert thread.is_actionable is False

    def test_resolved_thread_is_never_actionable(self):
        # Scenario: Resolved thread is never actionable
        thread = build_thread(make_thread("T6", "src/foo.ts", 1, 1, "fix!: broken null check", resolved=True))
        assert thread.is_actionable is False

    def test_thread_with_no_recognized_label_is_not_actionable(self):
        # Scenario: Thread with no recognized label is NOT actionable
        thread = build_thread(make_thread("T7", "src/foo.ts", 1, 1, "looks fine"))
        assert thread.is_actionable is False

    def test_last_comment_is_excluded_question_thread_not_actionable(self):
        # Scenario: Last comment is excluded (question!) — thread not actionable
        thread = build_thread(make_thread_multi_comments(
            "T8", "src/foo.ts", 10,
            "fix!: issue",
            "question!: Clarification?",
        ))
        assert thread.is_actionable is False

    def test_last_comment_is_excluded_fixed_thread_not_actionable(self):
        # Scenario: Last comment is excluded (Fixed.) — thread not actionable
        thread = build_thread(make_thread_multi_comments(
            "T9", "src/foo.ts", 10,
            "fix!: issue",
            "Fixed.",
        ))
        assert thread.is_actionable is False

    def test_unlabeled_comment_after_fix_thread_not_actionable(self):
        # Scenario: Unlabeled comment after fix! — thread not actionable
        thread = build_thread(make_thread_multi_comments(
            "T10", "src/foo.ts", 10,
            "fix!: broken null check",
            "I think this is fine actually",
        ))
        assert thread.is_actionable is False

    def test_fix_after_question_last_fix_wins_thread_actionable(self):
        # Scenario: fix! after question! — last fix wins, thread actionable
        thread = build_thread(make_thread_multi_comments(
            "T11", "src/foo.ts", 10,
            "fix!: original issue",
            "question!: Clarification?",
            "fix!: new fix",
        ))
        assert thread.is_actionable is True

    def test_fix_after_fixed_last_fix_wins_thread_actionable(self):
        # Scenario: fix! after Fixed. — last fix wins, thread actionable
        thread = build_thread(make_thread_multi_comments(
            "T12", "src/foo.ts", 10,
            "fix!: old issue",
            "Fixed.",
            "fix!: actually not fixed",
        ))
        assert thread.is_actionable is True


class TestActionableCommentExtraction:
    """Feature: Actionable Comment Extraction"""

    def test_single_labeled_comment_is_the_actionable_comment(self):
        # Scenario: Single labeled comment is the actionable comment
        thread = build_thread(make_thread("T1", "src/foo.ts", 1, 1, "fix!: broken null check"))
        assert thread.actionable_comment == "fix!: broken null check"

    def test_last_labeled_comment_is_used_when_multiple_comments_exist(self):
        # Scenario: Last labeled comment is used when multiple comments exist
        thread = build_thread(make_thread_multi_comments(
            "T2", "src/foo.ts", 20,
            "LGTM overall",
            "fix!: but this part is wrong",
        ))
        assert thread.actionable_comment == "fix!: but this part is wrong"

    def test_no_labeled_comment_actionable_comment_is_empty_string(self):
        # Scenario: No labeled comment — actionable_comment is empty string
        thread = build_thread(make_thread_multi_comments(
            "T3", "src/foo.ts", 1,
            "looks fine",
            "no issues",
        ))
        assert thread.actionable_comment == ""

    def test_empty_comment_list_returns_empty_string(self):
        # Scenario: Empty comment list returns empty string
        thread = ReviewThread(
            thread_id="T4", path="src/foo.ts", lines="1-1",
            is_resolved=False, comments=[],
        )
        assert thread.actionable_comment == ""


class TestThreadFilter:
    """Feature: Thread Filter"""

    def test_only_actionable_threads_are_returned(self):
        # Scenario: Only actionable threads are returned
        threads = [
            build_thread(make_thread("T1", "a.ts", 1, 1, "fix!: broken null check")),
            build_thread(make_thread("T2", "b.ts", 1, 1, "nit: minor style")),
            build_thread(make_thread("T3", "c.ts", 1, 1, "suggest!: extract method")),
            build_thread(make_thread("T4", "d.ts", 1, 1, "fix!: issue", resolved=True)),
        ]
        result = ThreadFilter().get_actionable_threads(threads)
        assert [t.thread_id for t in result] == ["T1", "T3"]

    def test_all_threads_are_non_actionable_empty_list_returned(self):
        # Scenario: All threads are non-actionable — empty list returned
        threads = [
            build_thread(make_thread("T1", "a.ts", 1, 1, "nit: minor style")),
            build_thread(make_thread("T2", "b.ts", 1, 1, "good: nice")),
        ]
        result = ThreadFilter().get_actionable_threads(threads)
        assert result == []

    def test_empty_input_returns_empty_list(self):
        # Scenario: Empty input returns empty list
        assert ThreadFilter().get_actionable_threads([]) == []

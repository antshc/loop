#!/usr/bin/env python3
"""Unit tests for the pr_discussion_state review-thread classifier.

Mapped to TEST_PLAN.md — every class docstring names the Feature,
every method name is the Scenario in snake_case.
When a test or scenario changes, update both sides to stay in sync.
"""

import modules.github.pr_discussion_state as pr_discussion_state


def _comment(author, created_at, review_state="__unset__", body="body"):
    comment = {
        "id": f"C-{author}-{created_at}",
        "author": {"login": author} if author else None,
        "body": body,
        "createdAt": created_at,
        "diffHunk": None,
    }
    if review_state != "__unset__":
        comment["pullRequestReview"] = {"state": review_state} if review_state is not None else None
    return comment


def _thread(comments, thread_id="T1", is_resolved=False, has_next_page=False):
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "isOutdated": False,
        "path": "file.py",
        "line": 10,
        "startLine": None,
        "originalLine": None,
        "originalStartLine": None,
        "diffSide": "RIGHT",
        "comments": {
            "totalCount": len(comments),
            "pageInfo": {"hasNextPage": has_next_page},
            "nodes": comments,
        },
    }


class TestReviewThreadClassification:
    """Feature: Review Thread Classification Ignores Pending Draft Comments"""

    def test_thread_with_only_pending_review_comments_is_dropped(self):
        # Scenario: A thread whose only comment belongs to a PENDING review is absent from the working set
        thread = _thread([_comment("bob", "t1", review_state="PENDING")])

        result = pr_discussion_state.classify(thread, "alice")

        assert result is None

    def test_submitted_review_comment_remains_pending(self):
        # Scenario: A comment attached to a submitted review remains pending
        thread = _thread([_comment("bob", "t1", review_state="COMMENTED")])

        result = pr_discussion_state.classify(thread, "alice")

        assert result["state"] == "pending"
        assert result["mode"] == "first-pass"

    def test_parentless_reply_remains_pending(self):
        # Scenario: An ordinary thread reply with no parent review remains pending
        thread = _thread([_comment("bob", "t1", review_state=None)])

        result = pr_discussion_state.classify(thread, "alice")

        assert result["state"] == "pending"
        assert result["mode"] == "first-pass"

    def test_pending_draft_comment_after_my_reply_does_not_reopen_thread(self):
        # Scenario: A draft comment after the acting user's published reply does not reopen the thread
        thread = _thread([
            _comment("bob", "t1", review_state="COMMENTED"),
            _comment("alice", "t2", review_state="COMMENTED"),
            _comment("bob", "t3", review_state="PENDING"),
        ])

        result = pr_discussion_state.classify(thread, "alice")

        assert result["state"] == "answered"
        assert result["lastAuthor"] == "alice"

    def test_pending_draft_comment_before_submitted_comment_does_not_alter_ordering(self):
        # Scenario: A draft comment before a submitted comment does not alter first-pass/follow-up ordering
        thread = _thread([
            _comment("bob", "t1", review_state="PENDING"),
            _comment("alice", "t2", review_state="COMMENTED"),
            _comment("bob", "t3", review_state="COMMENTED"),
        ])

        result = pr_discussion_state.classify(thread, "alice")

        assert result["mode"] == "follow-up"
        assert result["myLastReply"]["author"] == "alice"
        assert [c["author"] for c in result["newComments"]] == ["bob"]

    def test_threads_query_selects_pull_request_review_state(self):
        # Scenario: The GraphQL query selects the parent review state for each comment
        assert "pullRequestReview { state }" in pr_discussion_state.THREADS_QUERY

    def test_self_fix_keyword_note_is_not_treated_as_answered(self):
        # Scenario: The acting user's own last comment containing fix!: stays pending
        thread = _thread([
            _comment("bob", "t1", review_state="COMMENTED"),
            _comment("alice", "t2", review_state="COMMENTED", body="fix!: still needs work"),
        ])

        result = pr_discussion_state.classify(thread, "alice")

        assert result["state"] == "pending"

    def test_self_fix_keyword_note_after_real_reply_surfaces_as_new_comment(self):
        # Scenario: A fix!: note after an earlier real reply is surfaced, not swallowed as the answer
        thread = _thread([
            _comment("bob", "t1", review_state="COMMENTED"),
            _comment("alice", "t2", review_state="COMMENTED", body="Fixed."),
            _comment("alice", "t3", review_state="COMMENTED", body="fix!: still needs work"),
        ])

        result = pr_discussion_state.classify(thread, "alice")

        assert result["mode"] == "follow-up"
        assert result["myLastReply"]["body"] == "Fixed."
        assert [c["body"] for c in result["newComments"]] == ["fix!: still needs work"]

    def test_self_fix_keyword_note_as_only_comment_is_first_pass(self):
        # Scenario: A fix!: note with no prior reply from the acting user is a first-pass thread
        thread = _thread([
            _comment("alice", "t1", review_state="COMMENTED", body="fix!: still needs work"),
        ])

        result = pr_discussion_state.classify(thread, "alice")

        assert result["mode"] == "first-pass"


class TestBuildStateDraftFiltering:
    """Feature: Build State Excludes Draft-Only Threads"""

    def test_draft_only_thread_yields_skip_action(self, monkeypatch):
        # Scenario: A PR with only draft-review comments produces a skip action and no threads
        monkeypatch.setattr(pr_discussion_state, "acting_login", lambda: "alice")
        monkeypatch.setattr(
            pr_discussion_state,
            "pr_metadata",
            lambda owner, repo, number: {
                "state": "OPEN",
                "isDraft": False,
                "url": "https://github.com/owner/repo/pull/1",
                "headRefName": "feature",
                "baseRefName": "main",
            },
        )
        monkeypatch.setattr(
            pr_discussion_state,
            "fetch_threads",
            lambda owner, repo, number: [_thread([_comment("bob", "t1", review_state="PENDING")])],
        )

        state, exit_code = pr_discussion_state.build_state("owner/repo#1")

        assert exit_code == 0
        assert state["action"] == "skip"
        assert state["threads"] == []

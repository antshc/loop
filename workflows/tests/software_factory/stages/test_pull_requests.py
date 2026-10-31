"""The pull request Stages: origin validation, the snapshot, the next-action decision, the publication (push without force, confirmed remote head, one reply per feedback item, reconciled so a re-run never posts twice) and the block hand-off."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from loop import Evidence, Fail, Flow, Nothing, Pass, Stage, handoff, stop

from tests.runtime.harness import command
from tests.runtime.support import NoInput
from tests.shared.fakes import comment_node, issue_comment, thread_node
from workflows.software_factory.events.ci import CiFixed
from workflows.software_factory.events.feedback import (
    FeedbackBatch,
    FeedbackHandled,
    IssueResolved,
    IssueUnclear,
    ReplyDraft,
)
from workflows.software_factory.events.pull_requests import (
    CiFailing,
    CopilotDue,
    CopilotRequested,
    NextAction,
    OriginValidated,
    PrBlocked,
    PullRequestClosed,
    PullRequestPolled,
    ReadinessCheck,
    Unsupported,
)
from workflows.software_factory.stages import pull_requests
from workflows.software_factory.stages.pull_requests import (
    DecideInput,
    PublishInput,
    publish_batch,
)
from workflows.tests.software_factory.stages.pr_support import (
    BRANCH,
    NUMBER,
    SLUG,
    TRUST,
    Crash,
    PrRig,
    reply_text,
    selected_batch,
    thread,
    workspace_at,
)
from workflows.tests.software_factory.stages.support import probe
from workflows.tests.software_factory.stages.watch_support import (
    WatchRig,
    failing,
    fingerprint_of,
    produces,
    watch,
)

EVIDENCE = Evidence("", "", False)


class Publication:
    """A pull request whose feature branch is checked out in the harness repository, with the replies drafted for its feedback."""

    def __init__(
        self, rig: PrRig, *, commit: bool = True, unclear: tuple[str, ...] = ()
    ) -> None:
        self.rig = rig
        self.batch = selected_batch(rig)
        command(("checkout", "-q", BRANCH), rig.repo.path)
        if commit:
            rig.repo.write("fix.txt", "fix\n")
            self.local = rig.repo.commit("fix")
        else:
            self.local = rig.head
        drafts = tuple(ReplyDraft(key, reply_text(key)) for key in self.batch.keys)
        self.resolved = (
            IssueResolved(
                self.batch.token,
                "i1",
                "fix-needed",
                tuple(d for d in drafts if d.key not in unclear),
            ),
        )
        self.unclear = (
            (
                IssueUnclear(
                    self.batch.token,
                    "i2",
                    "why",
                    tuple(d for d in drafts if d.key in unclear),
                ),
            )
            if unclear
            else ()
        )

    def work(self) -> PublishInput:
        polled = PullRequestPolled(
            self.rig.target,
            NUMBER,
            {"pr": {"head": {"sha": self.batch.head, "ref": BRANCH}}},
        )
        return PublishInput(
            workspace_at(self.rig.repo.path),
            self.batch,
            polled,
            self.resolved,
            self.unclear,
            self.rig.git,
            self.rig.github,
        )

    def publish(self):
        stage = publish_batch()
        work = self.work()
        reply = stage.executor.run(work)
        return stage.judge(work, reply, EVIDENCE)

    def thread_replies(self) -> list[str]:
        return [
            comment["body"]
            for pull_thread in self.rig.pull.threads
            for comment in pull_thread["comments"][1:]
        ]


def test_a_batch_is_pushed_without_force_confirmed_and_each_item_is_replied_to_once(
    tmp_path: Path,
) -> None:
    """Given two threads and a Comment, when published, then the head is pushed without force, the remote head equals the local head and every item gets one reply."""
    rig = PrRig(tmp_path, threads=[thread("t1", "One"), thread("t2", "Two")])
    rig.pull.comments = [issue_comment(501, "reviewer", "Please add docs")]
    publication = Publication(rig)

    verdict = publication.publish()

    keys = publication.batch.keys
    assert verdict == Pass(FeedbackHandled(keys, publication.local))
    assert rig.runner.git("push") == [
        ("git", "push", "origin", f"HEAD:refs/heads/{BRANCH}")
    ]
    assert rig.remote_head(BRANCH) == publication.local
    assert publication.thread_replies() == [reply_text(keys[0]), reply_text(keys[1])]
    assert [comment["body"] for comment in rig.pull.comments[1:]] == [
        f"In response to https://github.com/a/b/pull/1#issuecomment-501:\n\n{reply_text(keys[2])}"
    ]


def test_a_batch_without_new_commits_pushes_nothing_and_still_replies(
    tmp_path: Path,
) -> None:
    """Given a worktree at the head the batch opened at, when published, then nothing is pushed and the item is replied to."""
    rig = PrRig(tmp_path)
    publication = Publication(rig, commit=False)

    verdict = publication.publish()

    assert isinstance(verdict, Pass)
    assert rig.runner.git("push") == [] and rig.remote_head(BRANCH) == rig.head
    assert publication.thread_replies() == [reply_text("thread:t1:t1c")]


def test_the_replies_of_an_unclear_issue_are_posted_with_the_rest(
    tmp_path: Path,
) -> None:
    """Given one resolved and one unclear issue, when published, then both issues' items are replied to."""
    rig = PrRig(tmp_path, threads=[thread("t1", "One"), thread("t2", "Two")])
    publication = Publication(rig, unclear=("thread:t2:t2c",))

    verdict = publication.publish()

    assert verdict.event.keys == ("thread:t1:t1c", "thread:t2:t2c")
    assert publication.thread_replies() == [
        reply_text("thread:t1:t1c"),
        reply_text("thread:t2:t2c"),
    ]


def test_a_crash_between_replies_is_reconciled_by_the_next_run_without_a_double_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a crash after the first of three replies, when the Stage runs again, then the missing replies are posted and no reply is posted twice."""
    rig = PrRig(
        tmp_path,
        threads=[thread("t1", "One"), thread("t2", "Two"), thread("t3", "Three")],
    )
    publication = Publication(rig)
    original = rig.host._reply

    def dies_on_the_second(thread_id: str, body: str) -> str:
        if len(rig.host.replies) == 1:
            raise Crash("the process died")
        return original(thread_id, body)

    monkeypatch.setattr(rig.host, "_reply", dies_on_the_second)
    with pytest.raises(Crash):
        publication.publish()
    pushed_once = rig.runner.git("push")
    monkeypatch.setattr(rig.host, "_reply", original)

    verdict = publication.publish()

    keys = publication.batch.keys
    assert verdict == Pass(FeedbackHandled(keys, publication.local))
    assert rig.host.replies == [reply_text(key) for key in keys]
    assert len(pushed_once) == 1 and rig.runner.git("push") == pushed_once


def test_a_finished_publication_is_recognized_and_posts_nothing_again(
    tmp_path: Path,
) -> None:
    """Given a publication that already finished, when the Stage reconciles and runs again, then it reports the same result and posts nothing."""
    rig = PrRig(tmp_path, threads=[thread("t1", "One"), thread("t2", "Two")])
    publication = Publication(rig)
    publication.publish()
    stage = publish_batch()

    reconciled = stage.executor.reconcile(publication.work())
    again = publication.publish()

    assert reconciled == FeedbackHandled(publication.batch.keys, publication.local)
    assert (
        isinstance(again, Pass)
        and len(rig.host.replies) == 2
        and len(rig.runner.git("push")) == 1
    )


def test_a_publication_that_is_not_finished_is_not_reconciled_as_done(
    tmp_path: Path,
) -> None:
    """Given a branch that was not pushed yet, when the Stage reconciles, then nothing counts as done."""
    rig = PrRig(tmp_path)
    publication = Publication(rig)

    assert publish_batch().executor.reconcile(publication.work()) is None


def test_a_thread_a_newer_comment_overtook_is_not_replied_to_and_not_handled(
    tmp_path: Path,
) -> None:
    """Given a thread that got a newer reviewer comment after the batch was selected, when published, then it is skipped while the other item is replied to."""
    rig = PrRig(tmp_path, threads=[thread("t1", "One"), thread("t2", "Two")])
    publication = Publication(rig)
    rig.pull.threads[0]["comments"].append(
        comment_node("t1d", "reviewer", "Actually, also this")
    )

    verdict = publication.publish()

    assert verdict == Pass(FeedbackHandled(("thread:t2:t2c",), publication.local))
    assert rig.host.replies == [reply_text("thread:t2:t2c")]


def test_a_resolved_thread_counts_as_handled_without_a_reply(tmp_path: Path) -> None:
    """Given a thread resolved after the batch was selected, when published, then it is handled and gets no reply."""
    rig = PrRig(tmp_path)
    publication = Publication(rig)
    rig.pull.threads[0]["isResolved"] = True

    verdict = publication.publish()

    assert verdict == Pass(FeedbackHandled(("thread:t1:t1c",), publication.local))
    assert rig.host.replies == []


REFUSALS = {
    "a closed pull request": (
        lambda pull: pull.close(),
        "PR closed during publication; inspect retained work before retrying",
    ),
    "a changed branch": (
        lambda pull: setattr(pull, "head_ref", "elsewhere"),
        "PR branch changed during publication",
    ),
    "a head somebody else moved": (
        lambda pull: setattr(pull, "head_sha", "f" * 40),
        "PR head moved; publication stopped before replying",
    ),
}


@pytest.mark.parametrize("change,reason", REFUSALS.values(), ids=REFUSALS.keys())
def test_a_publication_the_pull_request_no_longer_allows_fails_before_pushing_or_replying(
    tmp_path: Path, change, reason: str
) -> None:
    """Given a pull request that changed underneath the batch, when published, then the Stage fails and neither pushes nor replies."""
    rig = PrRig(tmp_path)
    publication = Publication(rig)
    change(rig.pull)

    verdict = publication.publish()

    assert verdict == Fail(reason)
    assert rig.runner.git("push") == [] and rig.host.replies == []


def test_a_push_the_remote_does_not_show_fails_without_replying(tmp_path: Path) -> None:
    """Given a remote head that never follows the push, when published, then the Stage fails with the confirmation error and posts no reply."""
    rig = PrRig(tmp_path)
    publication = Publication(rig)
    rig.pull.head_sha = rig.head

    verdict = publication.publish()

    assert verdict == Fail("Published SHA could not be confirmed")
    assert rig.host.replies == []


def test_a_ci_fix_is_published_without_replies_and_pushed_like_a_batch(
    tmp_path: Path,
) -> None:
    """Given a committed CI fix on the pull request head, when published, then the commit is pushed, the remote head equals it and nothing is replied to."""
    rig = WatchRig(tmp_path)
    command(("checkout", "-q", BRANCH), rig.repo.path)
    rig.repo.write("fix.txt", "fix\n")
    local = rig.repo.commit("fix ci")
    work = PublishInput(
        workspace_at(rig.repo.path),
        CiFixed(rig.head, "repaired"),
        rig.polled(),
        (),
        (),
        rig.git,
        rig.github,
    )

    verdict = publish_batch().judge(work, publish_batch().executor.run(work), EVIDENCE)

    assert verdict == Pass(FeedbackHandled((), local))
    assert rig.remote_head(BRANCH) == local and rig.host.replies == []


# ---- validate_origin -----------------------------------------------------------------------------------------


def test_validate_origin_passes_when_the_checkouts_origin_is_the_pull_requests_repository(
    tmp_path: Path,
) -> None:
    """Given a checkout whose origin is the pull request's repository, when origin is validated, then the run completes and records the slug."""
    rig = WatchRig(tmp_path)
    command(
        ("remote", "set-url", "origin", f"git@github.com:{SLUG.upper()}.git"),
        rig.repo.path,
    )

    seen: list[Any] = []

    result = rig.run_watch(
        watch(pull_requests.validate_origin(), probe(OriginValidated, seen))
    )

    assert result.status == "completed" and seen == [OriginValidated(SLUG.upper())]


def test_validate_origin_holds_when_the_origin_is_another_repository(
    tmp_path: Path,
) -> None:
    """Given a checkout of another repository, when origin is validated, then the run holds with the mismatch and no later Stage runs."""
    rig = WatchRig(tmp_path)
    command(
        ("remote", "set-url", "origin", "https://github.com/other/repo.git"),
        rig.repo.path,
    )
    reached: list[str] = []

    result = rig.run_watch(
        watch(pull_requests.validate_origin(), reached_stage(reached))
    )

    assert (
        result.status == "held"
        and result.reason == f"Origin does not match {SLUG}: {rig.repo.path}"
    )
    assert reached == []


# ---- snapshot ------------------------------------------------------------------------------------------------


def test_snapshot_reads_the_pull_request_with_babysits_projection_and_the_acting_account(
    tmp_path: Path,
) -> None:
    """Given an open pull request, when snapshotted, then the Event carries its target, number, the projected dictionaries as plain JSON and the acting login."""
    rig = WatchRig(tmp_path, threads=[thread("t1", "One")])

    seen: list[Any] = []

    result = rig.run_watch(
        watch(pull_requests.snapshot(), probe(PullRequestPolled, seen))
    )

    (polled,) = seen
    assert result.status == "completed"
    assert (
        polled.target,
        polled.number,
        polled.login,
        polled.head,
        polled.is_open,
    ) == (rig.target, NUMBER, "me", rig.head, True)
    assert set(polled.snapshot) == {
        "pr",
        "threads",
        "other",
        "runs",
        "checks",
        "statuses",
        "review",
    }
    assert [entry["id"] for entry in polled.snapshot["threads"]] == ["t1"]
    assert polled.snapshot == json.loads(json.dumps(polled.snapshot))


def test_snapshot_of_a_closed_pull_request_has_no_discussion_or_checks(
    tmp_path: Path,
) -> None:
    """Given a closed pull request, when snapshotted, then it is not open and carries no threads, checks or review summary."""
    rig = WatchRig(tmp_path, threads=[thread("t1", "One")])
    rig.pull.close(merged=True)

    polled = rig.polled()

    assert (
        not polled.is_open
        and polled.snapshot["threads"] == []
        and "review" not in polled.snapshot
    )


# ---- decide_next ---------------------------------------------------------------------------------------------


def decide(
    rig: WatchRig,
    *,
    handled: tuple[FeedbackHandled, ...] = (),
    requested: tuple[CopilotRequested, ...] = (),
    author: str | None = None,
    copilot: Callable[[PullRequestPolled], bool] = lambda _: False,
) -> NextAction:
    """What decide_next makes of the pull request as it stands, with the given history."""
    stage = pull_requests.decide_next(trust=TRUST, author=author, copilot=copilot)
    action = stage.executor.run(DecideInput(rig.polled(), handled, requested))
    verdict = stage.judge(
        DecideInput(rig.polled(), handled, requested), action, EVIDENCE
    )
    assert verdict == Pass(action)
    return action


def test_a_closed_pull_request_is_decided_as_closed_whatever_else_is_pending(
    tmp_path: Path,
) -> None:
    """Given a merged pull request that still has feedback and failed CI, when the next action is decided, then it is the closed one with its head and merge state."""
    rig = WatchRig(tmp_path, threads=[thread("t1", "One")])
    rig.fail_ci()
    rig.pull.close(merged=True)

    assert decide(rig) == PullRequestClosed(rig.head, True)


@pytest.mark.parametrize(
    ("change", "author"),
    [
        (lambda pull: setattr(pull, "author", "someone-else"), None),
        (lambda pull: setattr(pull, "head_repo", "fork/repo"), None),
        (lambda pull: None, "a-different-owner"),
    ],
    ids=[
        "another author",
        "a fork head",
        "a configured author that is not the pull request's",
    ],
)
def test_a_pull_request_of_another_author_or_a_fork_is_unsupported(
    tmp_path: Path, change, author
) -> None:
    """Given a pull request this checkout cannot maintain, when the next action is decided, then it is unsupported even with feedback pending."""
    rig = WatchRig(tmp_path, threads=[thread("t1", "One")])
    change(rig.pull)

    assert decide(rig, author=author) == Unsupported(
        "PR author changed or fork head is not supported by this checkout"
    )


def test_a_configured_author_replaces_the_acting_account_as_the_expected_author(
    tmp_path: Path,
) -> None:
    """Given a pull request by a configured author other than the acting account, when the next action is decided, then it is supported."""
    rig = WatchRig(tmp_path)
    rig.pull.author = "robot"

    assert decide(rig, author="Robot") == ReadinessCheck(rig.head, True)


def test_trusted_feedback_no_earlier_run_handled_is_a_batch_of_only_those_items(
    tmp_path: Path,
) -> None:
    """Given three trusted threads, one handled earlier, and an untrusted drive-by, when the next action is decided, then the batch holds the two unhandled trusted items."""
    rig = WatchRig(
        tmp_path,
        threads=[thread("t1", "One"), thread("t2", "Two"), thread("t3", "Three")],
    )
    rig.pull.threads.append(
        thread_node(
            [comment_node("t4c", "outsider", "Drive-by", association="NONE")], id="t4"
        )
    )

    action = decide(rig, handled=(FeedbackHandled(("thread:t1:t1c",), "earlier"),))

    assert isinstance(action, FeedbackBatch)
    assert (
        action.keys == ("thread:t2:t2c", "thread:t3:t3c")
        and action.head == rig.head
        and action.ci_failing is False
    )
    assert all(entry["status"] == "pending" for entry in action.items)


def test_feedback_handled_in_an_earlier_run_is_not_offered_again(
    tmp_path: Path,
) -> None:
    """Given the only feedback handled earlier, when the next action is decided, then there is no batch."""
    rig = WatchRig(tmp_path, threads=[thread("t1", "One")])

    assert decide(
        rig, handled=(FeedbackHandled(("thread:t1:t1c",), "earlier"),)
    ) == ReadinessCheck(rig.head, True)


def test_failed_ci_comes_after_feedback_and_before_copilot_and_readiness(
    tmp_path: Path,
) -> None:
    """Given failed CI with and without pending feedback, when the next action is decided, then feedback wins and otherwise CI is failing even if Copilot is due."""
    rig = WatchRig(tmp_path)
    rig.fail_ci()

    assert decide(rig, copilot=lambda _: True) == CiFailing(rig.head)

    rig.pull.threads = [thread("t1", "One")]
    batch = decide(rig, copilot=lambda _: True)
    assert isinstance(batch, FeedbackBatch) and batch.ci_failing is True


def test_a_copilot_review_is_due_for_a_head_it_was_not_requested_on_when_enabled(
    tmp_path: Path,
) -> None:
    """Given an enabled Copilot review, when the head was not requested, then it is due; once requested, or when disabled, it is a readiness check."""
    rig = WatchRig(tmp_path)
    requested = (CopilotRequested("another-head", "requested"),)

    assert decide(rig, requested=requested, copilot=lambda _: True) == CopilotDue(
        rig.head
    )
    assert decide(
        rig,
        requested=(*requested, CopilotRequested(rig.head, "unavailable")),
        copilot=lambda _: True,
    ) == ReadinessCheck(rig.head, True)
    assert decide(rig, requested=requested) == ReadinessCheck(rig.head, True)


READINESS = {
    "a clean pull request": (lambda rig: None, True),
    "pending CI": (lambda rig: rig.pend_ci(), False),
    "a draft": (lambda rig: setattr(rig.pull, "draft", True), False),
    "a pending Copilot review": (
        lambda rig: rig.pull.requested_reviewers.append(
            "copilot-pull-request-reviewer[bot]"
        ),
        False,
    ),
    "a review that is still required": (
        lambda rig: setattr(rig.pull, "review_decision", "REVIEW_REQUIRED"),
        False,
    ),
    "a requested change": (
        lambda rig: setattr(rig.pull, "review_decision", "CHANGES_REQUESTED"),
        False,
    ),
    "a conflicting merge state": (
        lambda rig: setattr(rig.pull, "mergeable_state", "dirty"),
        False,
    ),
    "an unknown mergeability": (
        lambda rig: setattr(rig.pull, "mergeable", None),
        False,
    ),
}


@pytest.mark.parametrize("change,ready", READINESS.values(), ids=READINESS.keys())
def test_with_nothing_to_do_the_readiness_check_says_whether_the_pull_request_can_merge(
    tmp_path: Path, change, ready: bool
) -> None:
    """Given a pull request with nothing to address, when the next action is decided, then readiness is true only for a clean, green, non-draft one with no pending review."""
    rig = WatchRig(tmp_path)
    change(rig)

    assert decide(rig) == ReadinessCheck(rig.head, ready)


# ---- block_pr ------------------------------------------------------------------------------------------------


def reached_stage(log: list[str]) -> Stage[Any, Any, Nothing]:
    """A check Stage that records it ran."""
    return (
        Stage("reached", NoInput, produces=Nothing)
        .action(lambda _: log.append("reached"))
        .gate(lambda *_: Pass(Nothing()))
    )


def test_the_block_hand_off_records_the_reason_and_the_fingerprint_on_the_run(
    tmp_path: Path,
) -> None:
    """Given a Stage that fails into the block hand-off, when the run ends, then it is handed off and the closed run carries the reason and the fingerprint the given function computed."""
    rig = WatchRig(tmp_path)
    block = pull_requests.block_pr(fingerprint=fingerprint_of)

    result = rig.run_watch(
        watch(
            pull_requests.snapshot(),
            failing("CI is red").on_fail(handoff(block)),
            produces(Unsupported("x")),
        )
    )

    assert result.status == "handed_off"
    assert rig.closed_run_attributes() == {
        "factory.pr.fingerprint": f"{rig.head}:0",
        "factory.pr.reason": "CI is red",
    }
    assert rig.recorded(PrBlocked) == (PrBlocked(f"{rig.head}:0", ""),)


def test_the_block_fingerprint_comes_from_a_fresh_read_so_our_own_replies_are_in_it(
    tmp_path: Path,
) -> None:
    """Given a reply posted after the last snapshot, when the pull request is blocked, then the fingerprint is computed on a read that includes it."""
    rig = WatchRig(tmp_path)
    replies = (
        Stage("reply", NoInput, produces=Nothing)
        .action(lambda _: rig.pull.comments.append(issue_comment(1, "me", "answered")))
        .gate(lambda *_: Pass(Nothing()))
    )
    block = pull_requests.block_pr(fingerprint=fingerprint_of)

    rig.run_watch(
        watch(
            pull_requests.snapshot(),
            replies,
            failing("stuck").on_fail(handoff(block)),
            produces(Unsupported("x")),
        )
    )

    assert rig.closed_run_attributes()["factory.pr.fingerprint"] == f"{rig.head}:1"


def test_the_block_fingerprint_falls_back_to_the_last_snapshot_when_the_pull_request_cannot_be_read(
    tmp_path: Path,
) -> None:
    """Given a GitHub read that fails at block time, when the pull request is blocked, then the fingerprint is computed on the last snapshot."""
    rig = WatchRig(tmp_path)
    rig.gh.fail_on("api", "user", after=1)
    block = pull_requests.block_pr(fingerprint=fingerprint_of)

    result = rig.run_watch(
        watch(
            pull_requests.snapshot(),
            failing("stuck").on_fail(handoff(block)),
            produces(Unsupported("x")),
        )
    )

    assert (
        result.status == "handed_off"
        and rig.closed_run_attributes()["factory.pr.fingerprint"] == f"{rig.head}:0"
    )


def test_the_block_fingerprint_is_empty_when_nothing_was_ever_read(
    tmp_path: Path,
) -> None:
    """Given a pull request that blocks before any snapshot and cannot be read, when blocked, then the fingerprint is empty and the reason is still recorded."""
    rig = WatchRig(tmp_path)
    rig.gh.fail_on("api", "user")
    block = pull_requests.block_pr(fingerprint=fingerprint_of)

    rig.run_watch(
        watch(
            failing("early").on_fail(handoff(block)),
            pull_requests.snapshot(),
            produces(Unsupported("x")),
        )
    )

    assert rig.closed_run_attributes() == {
        "factory.pr.fingerprint": "",
        "factory.pr.reason": "early",
    }


def test_an_unsupported_pull_request_dispatched_to_the_block_hand_off_records_why(
    tmp_path: Path,
) -> None:
    """Given a pull request of another author, when the decision is dispatched to the block hand-off, then the run is handed off with the unsupported reason as its reason attribute."""
    rig = WatchRig(tmp_path)
    rig.pull.author = "someone-else"
    block = pull_requests.block_pr(fingerprint=fingerprint_of)
    cases = {
        variant: stop.completed()
        for variant in (PullRequestClosed, FeedbackBatch, CiFailing, ReadinessCheck)
    }
    elsewhere = (
        Flow("elsewhere")
        .stage(produces(FeedbackHandled((), "h")))
        .stage(produces(CopilotRequested("h", "requested")))
    )
    workflow = watch(
        pull_requests.snapshot(),
        pull_requests.decide_next(trust=TRUST, author=None, copilot=lambda _: False),
    ).dispatch(
        NextAction, {**cases, CopilotDue: elsewhere, Unsupported: handoff(block)}
    )

    result = rig.run_watch(workflow)

    assert result.status == "handed_off"
    assert (
        rig.closed_run_attributes()["factory.pr.reason"]
        == "PR author changed or fork head is not supported by this checkout"
    )

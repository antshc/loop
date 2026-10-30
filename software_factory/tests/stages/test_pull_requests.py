"""The publication Stage: the push without force, the confirmed remote head and one reply per feedback item, reconciled so a re-run never posts twice."""

from __future__ import annotations

from pathlib import Path

import pytest

from software_factory.events.feedback import FeedbackHandled, IssueResolved, IssueUnclear, ReplyDraft
from software_factory.events.pull_requests import PullRequestPolled
from software_factory.runtime import Evidence, Fail, Pass
from software_factory.stages.pull_requests import PublishInput, publish_batch
from software_factory.tests.runtime.harness import command
from software_factory.tests.shared.fakes import comment_node, issue_comment
from software_factory.tests.stages.pr_support import BRANCH, NUMBER, Crash, PrRig, reply_text, selected_batch, thread, workspace_at

EVIDENCE = Evidence("", "", False)


class Publication:
    """A pull request whose feature branch is checked out in the harness repository, with the replies drafted for its feedback."""

    def __init__(self, rig: PrRig, *, commit: bool = True, unclear: tuple[str, ...] = ()) -> None:
        self.rig = rig
        self.batch = selected_batch(rig)
        command(("checkout", "-q", BRANCH), rig.repo.path)
        if commit:
            rig.repo.write("fix.txt", "fix\n")
            self.local = rig.repo.commit("fix")
        else:
            self.local = rig.head
        drafts = tuple(ReplyDraft(key, reply_text(key)) for key in self.batch.keys)
        self.resolved = (IssueResolved(self.batch.token, "i1", "fix-needed", tuple(d for d in drafts if d.key not in unclear)),)
        self.unclear = (IssueUnclear(self.batch.token, "i2", "why", tuple(d for d in drafts if d.key in unclear)),) if unclear else ()

    def work(self) -> PublishInput:
        polled = PullRequestPolled(self.rig.target, NUMBER, {"pr": {"head": {"sha": self.batch.head, "ref": BRANCH}}})
        return PublishInput(
            workspace_at(self.rig.repo.path), self.batch, polled, self.resolved, self.unclear, self.rig.git, self.rig.github,
        )

    def publish(self):
        stage = publish_batch()
        work = self.work()
        reply = stage.executor.run(work)
        return stage.judge(work, reply, EVIDENCE)

    def thread_replies(self) -> list[str]:
        return [comment["body"] for pull_thread in self.rig.pull.threads for comment in pull_thread["comments"][1:]]


def test_a_batch_is_pushed_without_force_confirmed_and_each_item_is_replied_to_once(tmp_path: Path) -> None:
    """Given two threads and a Comment, when published, then the head is pushed without force, the remote head equals the local head and every item gets one reply."""
    rig = PrRig(tmp_path, threads=[thread("t1", "One"), thread("t2", "Two")])
    rig.pull.comments = [issue_comment(501, "reviewer", "Please add docs")]
    publication = Publication(rig)

    verdict = publication.publish()

    keys = publication.batch.keys
    assert verdict == Pass(FeedbackHandled(keys, publication.local))
    assert rig.runner.git("push") == [("git", "push", "origin", f"HEAD:refs/heads/{BRANCH}")]
    assert rig.remote_head(BRANCH) == publication.local
    assert publication.thread_replies() == [reply_text(keys[0]), reply_text(keys[1])]
    assert [comment["body"] for comment in rig.pull.comments[1:]] == [
        f"In response to https://github.com/a/b/pull/1#issuecomment-501:\n\n{reply_text(keys[2])}"
    ]


def test_a_batch_without_new_commits_pushes_nothing_and_still_replies(tmp_path: Path) -> None:
    """Given a worktree at the head the batch opened at, when published, then nothing is pushed and the item is replied to."""
    rig = PrRig(tmp_path)
    publication = Publication(rig, commit=False)

    verdict = publication.publish()

    assert isinstance(verdict, Pass)
    assert rig.runner.git("push") == [] and rig.remote_head(BRANCH) == rig.head
    assert publication.thread_replies() == [reply_text("thread:t1:t1c")]


def test_the_replies_of_an_unclear_issue_are_posted_with_the_rest(tmp_path: Path) -> None:
    """Given one resolved and one unclear issue, when published, then both issues' items are replied to."""
    rig = PrRig(tmp_path, threads=[thread("t1", "One"), thread("t2", "Two")])
    publication = Publication(rig, unclear=("thread:t2:t2c",))

    verdict = publication.publish()

    assert verdict.event.keys == ("thread:t1:t1c", "thread:t2:t2c")
    assert publication.thread_replies() == [reply_text("thread:t1:t1c"), reply_text("thread:t2:t2c")]


def test_a_crash_between_replies_is_reconciled_by_the_next_run_without_a_double_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a crash after the first of three replies, when the Stage runs again, then the missing replies are posted and no reply is posted twice."""
    rig = PrRig(tmp_path, threads=[thread("t1", "One"), thread("t2", "Two"), thread("t3", "Three")])
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


def test_a_finished_publication_is_recognized_and_posts_nothing_again(tmp_path: Path) -> None:
    """Given a publication that already finished, when the Stage reconciles and runs again, then it reports the same result and posts nothing."""
    rig = PrRig(tmp_path, threads=[thread("t1", "One"), thread("t2", "Two")])
    publication = Publication(rig)
    publication.publish()
    stage = publish_batch()

    reconciled = stage.executor.reconcile(publication.work())
    again = publication.publish()

    assert reconciled == FeedbackHandled(publication.batch.keys, publication.local)
    assert isinstance(again, Pass) and len(rig.host.replies) == 2 and len(rig.runner.git("push")) == 1


def test_a_publication_that_is_not_finished_is_not_reconciled_as_done(tmp_path: Path) -> None:
    """Given a branch that was not pushed yet, when the Stage reconciles, then nothing counts as done."""
    rig = PrRig(tmp_path)
    publication = Publication(rig)

    assert publish_batch().executor.reconcile(publication.work()) is None


def test_a_thread_a_newer_comment_overtook_is_not_replied_to_and_not_handled(tmp_path: Path) -> None:
    """Given a thread that got a newer reviewer comment after the batch was selected, when published, then it is skipped while the other item is replied to."""
    rig = PrRig(tmp_path, threads=[thread("t1", "One"), thread("t2", "Two")])
    publication = Publication(rig)
    rig.pull.threads[0]["comments"].append(comment_node("t1d", "reviewer", "Actually, also this"))

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
    "a closed pull request": (lambda pull: pull.close(), "PR closed during publication; inspect retained work before retrying"),
    "a changed branch": (lambda pull: setattr(pull, "head_ref", "elsewhere"), "PR branch changed during publication"),
    "a head somebody else moved": (lambda pull: setattr(pull, "head_sha", "f" * 40), "PR head moved; publication stopped before replying"),
}


@pytest.mark.parametrize("change,reason", REFUSALS.values(), ids=REFUSALS.keys())
def test_a_publication_the_pull_request_no_longer_allows_fails_before_pushing_or_replying(tmp_path: Path, change, reason: str) -> None:
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

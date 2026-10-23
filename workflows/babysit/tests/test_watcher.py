from __future__ import annotations

from copy import deepcopy

from workflows.babysit.watcher import ci_state, observe, pending_threads
from workflows.pr_maintenance.state import PrState


def comment(id="c1", login="reviewer", body="Check this"):
    return {"id": id, "body": body, "author": {"login": login}, "authorAssociation": "COLLABORATOR"}


def thread(comments=None, resolved=False):
    return {"id": "t1", "isResolved": resolved, "isOutdated": False, "path": "cart.py", "line": 4,
            "comments": comments if comments is not None else [comment()]}


def test_gate_skips_fixed_and_resolved_but_accepts_followups_and_self_notes():
    fixed = thread([comment(), comment("c2", "me", "Fixed!")])
    assert pending_threads([fixed, thread(resolved=True)], "me") == []
    fixed["comments"].append(comment("c3", body="Also handle empty cart"))
    pending = pending_threads([fixed], "me")
    assert [c["id"] for c in pending[0]["comments"]] == ["c3"]
    assert pending[0]["previous_reply"]["body"] == "Fixed!"
    assert pending_threads([thread([comment(login="me")])], "me")
    assert pending_threads([thread([comment(), comment("c2", "me", "fix!: follow up")])], "me")


def test_seen_is_not_handled_and_sha_change_does_not_reopen(tmp_path):
    state = PrState(tmp_path, "a/b", 1)
    snapshot = {"threads": [thread()], "other": []}
    first = observe(snapshot, state, "me")
    assert observe(snapshot, PrState(tmp_path, "a/b", 1), "me") == first
    state.data["items"][first[0]["key"]]["status"] = "handled"
    state.save()
    snapshot["pr"] = {"head": {"sha": "new"}}
    assert observe(snapshot, state, "me") == []
    snapshot["threads"][0]["comments"].append(comment("c2"))
    assert len(observe(snapshot, state, "me")) == 1


def test_latest_ci_attempt_supersedes_previous_failure():
    old = {"id": 1, "name": "test", "status": "completed", "conclusion": "failure", "app": {"id": 1}}
    new = {**old, "id": 2, "conclusion": "success"}
    snapshot = {"checks": [old, new], "statuses": [], "runs": []}
    assert ci_state(snapshot)["green"]
    snapshot["checks"].append({**new, "id": 3, "status": "in_progress", "conclusion": None})
    assert ci_state(snapshot)["pending"]


def test_untrusted_feedback_is_not_dispatched(tmp_path):
    untrusted = comment(login="outsider")
    untrusted["authorAssociation"] = "NONE"
    assert not observe({"threads": [thread([untrusted])], "other": []}, PrState(tmp_path, "a/b", 1), "me")

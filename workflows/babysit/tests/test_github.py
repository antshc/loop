from __future__ import annotations

import json
import subprocess

import pytest

from workflows.pr_maintenance.github import Github


def test_rest_collection_paginates_and_filters_author():
    commands = []
    def invoke(argv, **kwargs):
        commands.append(argv)
        payload = [[{"user": {"login": "Me"}, "number": 1}], [{"user": {"login": "other"}, "number": 2}]]
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload))
    assert [pr["number"] for pr in Github(run=invoke).open_prs("a/b", "me")] == [1]
    assert "--paginate" in commands[0] and "--slurp" in commands[0]


def test_thread_and_comment_pagination_omits_empty_initial_cursor():
    commands = []
    def invoke(argv, **kwargs):
        commands.append(argv)
        if any("query=query($id" in arg for arg in argv):
            payload = {"data": {"node": {"comments": {"nodes": [{"id": "c2"}],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}
        elif "cursor=next-thread" in argv:
            payload = {"data": {"repository": {"pullRequest": {"reviewThreads": {
                "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}
        else:
            assert "cursor=" not in argv
            payload = {"data": {"repository": {"pullRequest": {"reviewThreads": {
                "nodes": [{"id": "t1", "comments": {"nodes": [{"id": "c1"}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "next-comment"}}}],
                "pageInfo": {"hasNextPage": True, "endCursor": "next-thread"}}}}}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload))
    threads = Github(run=invoke).threads("a/b", 1)
    assert [c["id"] for c in threads[0]["comments"]] == ["c1", "c2"]
    assert len(commands) == 3


def test_graphql_mutation_errors_cannot_be_marked_successful():
    def invoke(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, '{"errors":[{"message":"forbidden"}]}')
    with pytest.raises(ValueError, match="forbidden"):
        Github(run=invoke).reply("a/b", 1, {"kind": "thread", "thread_id": "t1"}, "Fixed!")

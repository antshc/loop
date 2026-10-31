from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from workflows.software_factory.infrastructure.github.identity import (
    CommentRef,
    IssueIdentity,
    IssueRef,
    RepositoryId,
)


def test_repository_id_contains_owner_and_repo_and_renders_slug() -> None:
    identity = RepositoryId("antshc", "loop")

    assert identity.owner == "antshc"
    assert identity.repo == "loop"
    assert str(identity) == "antshc/loop"
    assert tuple(identity) == ("antshc", "loop")


def test_an_issue_is_addressed_by_its_repository_and_number() -> None:
    ref = IssueRef(RepositoryId("antshc", "loop"), 155)

    assert (ref.repository, ref.number) == (RepositoryId("antshc", "loop"), 155)
    assert str(ref) == "antshc/loop#155"
    assert ref == IssueRef(RepositoryId("antshc", "loop"), 155)


def test_a_comment_is_addressed_by_its_issue_and_its_own_id() -> None:
    issue = IssueRef(RepositoryId("antshc", "loop"), 155)

    assert CommentRef(issue, 9).issue == issue and CommentRef(issue, 9).id == 9
    assert CommentRef(issue, 9) != CommentRef(issue, 10)


def test_identity_carries_the_rest_id_and_the_node_id() -> None:
    identity = IssueIdentity(1155, "I_kwDO155")

    assert (identity.id, identity.node_id) == (1155, "I_kwDO155")
    with pytest.raises(FrozenInstanceError):
        setattr(identity, "id", 1)

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from workflows.dev.model.records import Repository, RepositoryId


def test_repository_id_contains_owner_and_repo_and_renders_slug() -> None:
    identity = RepositoryId("antshc", "loop")

    assert identity.owner == "antshc"
    assert identity.repo == "loop"
    assert str(identity) == "antshc/loop"
    assert tuple(identity) == ("antshc", "loop")


def test_repository_holds_explicit_paths_and_owner_name(tmp_path: Path) -> None:
    worktrees = tmp_path / "workspace" / "harness.worktrees"
    identity = RepositoryId("owner", "repo")

    repository = Repository(checkout=tmp_path, worktrees=worktrees, owner_name=identity)

    assert repository.checkout == tmp_path
    assert repository.worktrees == worktrees
    assert repository.owner_name == identity
    with pytest.raises(FrozenInstanceError):
        setattr(repository, "checkout", tmp_path / "other")

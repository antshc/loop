from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from workflows.implement_spec.model.records import Repository
from workflows.shared.github.identity import RepositoryId


def test_repository_holds_explicit_paths_and_owner_name(tmp_path: Path) -> None:
    worktrees = tmp_path / "workspace" / "harness.worktrees"
    identity = RepositoryId("owner", "repo")

    repository = Repository(checkout=tmp_path, worktrees=worktrees, owner_name=identity)

    assert repository.checkout == tmp_path
    assert repository.worktrees == worktrees
    assert repository.owner_name == identity
    with pytest.raises(FrozenInstanceError):
        setattr(repository, "checkout", tmp_path / "other")

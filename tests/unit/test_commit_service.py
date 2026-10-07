from __future__ import annotations

from pathlib import Path

from conftest import commit_file, git
from conftest import init_pushed_repo as _init_pushed_repo
from loop import Branch, CommitService


def test_head_returns_the_full_hash_subject_and_body(repo: Path) -> None:
    commit_file(repo, "a.txt", "1", "subject line\n\nbody line one\nbody line two")

    commit = CommitService().head(repo)

    assert commit.sha == git(repo, "rev-parse", "HEAD")
    assert commit.subject == "subject line"
    assert commit.body == "body line one\nbody line two"


def test_since_lists_every_new_commit_oldest_first_including_a_merge(repo: Path) -> None:
    service = CommitService()
    base = service.head(repo)
    git(repo, "checkout", "-b", "feature")
    commit_file(repo, "feature.txt", "1", "feature work")
    git(repo, "checkout", "main")
    commit_file(repo, "main.txt", "1", "main work")
    git(repo, "merge", "--no-ff", "-m", "merge feature", "feature")

    commits = service.since(base)

    assert [commit.subject for commit in commits] == ["feature work", "main work", "merge feature"]


def test_since_returns_an_empty_list_when_nothing_was_committed(repo: Path) -> None:
    service = CommitService()
    base = service.head(repo)

    assert service.since(base) == []


def test_find_since_excludes_a_commit_whose_body_but_not_subject_carries_the_prefix(tmp_path: Path) -> None:
    checkout = _init_pushed_repo(tmp_path / "harness")
    service = CommitService()
    commit_file(checkout, "a.txt", "1", "ccode(Init|1): first")
    commit_file(checkout, "b.txt", "2", "unrelated")
    commit_file(checkout, "c.txt", "3", "subject\n\nccode(Init|1): only in the body")
    commit_file(checkout, "d.txt", "4", "ccode(Init|1): second")

    commits = service.find_since(Branch(checkout, "main"), subject_prefix="ccode(Init|1): ")

    assert [commit.subject for commit in commits] == ["ccode(Init|1): first", "ccode(Init|1): second"]


def test_restore_discards_later_commits_uncommitted_changes_and_untracked_files_and_folders(repo: Path) -> None:
    service = CommitService()
    recorded = service.head(repo)
    commit_file(repo, "a.txt", "a\n", "extra commit")
    (repo / "b.txt").write_text("dirty\n")
    git(repo, "add", "b.txt")
    (repo / "untracked.txt").write_text("untracked\n")
    (repo / "untracked_dir").mkdir()
    (repo / "untracked_dir" / "c.txt").write_text("c\n")

    service.restore(recorded)

    assert service.head(repo).sha == recorded.sha
    assert git(repo, "status", "--porcelain") == ""
    assert not (repo / "a.txt").exists()
    assert not (repo / "b.txt").exists()
    assert not (repo / "untracked.txt").exists()
    assert not (repo / "untracked_dir").exists()


def test_identity_reads_the_configured_name_and_email(repo: Path) -> None:
    assert CommitService().identity(repo) == ("Test", "test@example.com")

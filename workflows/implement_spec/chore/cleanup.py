"""The chore step: capture the published pull request's diff on the worktree, run a cleanup agent, publish its commit."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

from loop import AgentWorkspace, Kind, Phases

from workflows.shared.git import Commit, GitClient
from workflows.shared.github.issues.graph import SpecGraphSnapshot
from workflows.shared.github.repo import GitHubRepo

from ..model.branches import feature_branch as feature_branch_of
from ..model.definition import Owner
from ..model.gates import commit_subject, made_no_commit
from ..model.outcomes import ChoreReport
from ..model.records import ChoreContext, ChoreResult, ChoreWorkflow, TicketWorkflowError, loop_session_name


logger = logging.getLogger("workflow.chore")


DIFF_DIR = Path("bin") / "review_diff"


MANIFEST_NAME = "_manifest.tsv"


_DIFF_HEADER = "diff --git "


_SAME_PATH_HEADER = re.compile(r"^diff --git a/(?P<path>.+) b/(?P=path)$")


_RENAMED_HEADER = re.compile(r"^diff --git a/.+ b/(?P<path>.+)$")


def capture_pull_request_diff(github: GitHubRepo, pull_request_url: str, worktree: Path) -> Path:
    """Writes one diff file per changed path, plus `_manifest.tsv` (`REAL_PATH<TAB>SANITIZED_FILENAME`), under `bin/review_diff` of `worktree`.

    The worktree already holds the PR branch, so no checkout is needed; the manifest is the only source of real paths.
    """
    diff = github.run(("pr", "diff", pull_request_url, "--repo", github.slug))
    directory = worktree / DIFF_DIR
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    manifest: list[str] = []
    for path, lines in _split_by_file(diff):
        sanitized = _unique_name(path.replace("/", "_"), directory)
        (directory / sanitized).write_text("".join(lines))
        manifest.append(f"{path}\t{sanitized}\n")
    (directory / MANIFEST_NAME).write_text("".join(manifest))
    return directory


def _split_by_file(diff: str) -> list[tuple[str, list[str]]]:
    files: list[tuple[str, list[str]]] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith(_DIFF_HEADER):
            files.append((_header_path(line.rstrip("\r\n")), []))
        if files:
            files[-1][1].append(line)
    return files


def _header_path(header: str) -> str:
    match = _SAME_PATH_HEADER.match(header) or _RENAMED_HEADER.match(header)
    if match is None:
        raise TicketWorkflowError(f"unparseable diff header: {header!r}")
    return match["path"]


def _unique_name(name: str, directory: Path) -> str:
    # Flattening `/` to `_` can map two paths to one name; the manifest still tells them apart.
    candidate, counter = name, 1
    while (directory / candidate).exists() or candidate == MANIFEST_NAME:
        counter += 1
        candidate = f"{counter}_{name}"
    return candidate


class ChoreRefiner:
    """Runs one cleanup agent after the PR exists; the harness commits its edits as one chore commit and pushes it."""

    def __init__(self, git: GitClient, phases: Phases) -> None:
        self._git = git
        self._phases = phases

    def refine(
        self, spec: SpecGraphSnapshot, pull_request_url: str, worktree: AgentWorkspace, run_chore_agent: ChoreWorkflow, github: GitHubRepo
    ) -> ChoreReport:
        """Best effort: any failure restores the worktree to the delivered commit and leaves the PR as it was."""
        git, phases = self._git, self._phases
        base_branch, feature_branch = spec.base_branch, feature_branch_of(spec)
        if base_branch is None or feature_branch is None:
            raise TicketWorkflowError(f"cannot resolve branches for Spec {spec.url}")
        head_before = git.head(worktree.path)
        result = ChoreResult()
        with phases.phase("chore", Kind.AGENT, Owner.CHORE, spec=spec.number) as phase:
            try:
                diff_dir = capture_pull_request_diff(github, pull_request_url, worktree.path)
                try:
                    commits = tuple(_describe(commit) for commit in git.branch_commits(worktree.path, base_branch))
                    context = ChoreContext(
                        pull_request_url, base_branch, feature_branch, diff_dir, commits,
                        loop_session_name(spec.number, spec.title),
                    )
                    result = run_chore_agent(context, worktree)
                finally:
                    shutil.rmtree(diff_dir, ignore_errors=True)
                violation = made_no_commit(git, worktree.path, head_before.sha)
            except (OSError, subprocess.CalledProcessError, TicketWorkflowError) as exception:
                violation = f"chore agent failed: {exception}"
            phase.gate("the agent did not commit", violation is None, violation or "")
        if violation is not None:
            logger.warning("chore for %s discarded: %s", pull_request_url, violation)
            with phases.phase("restore", Kind.CODE, Owner.CODE, to=head_before.sha):
                git.restore(worktree.path, head_before.sha)
            return ChoreReport("discarded", violation, result.out_of_scope)
        if not git.changed_files(worktree.path):
            return ChoreReport("nothing", result.summary, result.out_of_scope)
        subject = commit_subject(f"chore({spec.initiative}): ", result.commit_message, "refine the change set")
        with phases.phase("commit-chore", Kind.CODE, Owner.CODE, spec=spec.number) as phase:
            commit = git.commit(worktree.path, subject, result.summary)
            phase.set(commit=commit.sha)
            git.push_if_ahead(worktree.path, feature_branch, base_branch)
        return ChoreReport("committed", result.summary, result.out_of_scope, commit.sha)


def _describe(commit: Commit) -> str:
    entry = f"{commit.sha[:7]} {commit.subject}"
    return f"{entry}\n{textwrap.indent(commit.body, '    ')}" if commit.body else entry

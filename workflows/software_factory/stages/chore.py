"""The chore Stage: refine the delivered change set on the feature worktree and let the runtime commit what passes verification."""

from __future__ import annotations

import re
import shutil
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loop import (
    AgentError,
    AgentWorkspace,
    Declined,
    Evidence,
    Fail,
    HarnessCommits,
    Pass,
    Restore,
    Stage,
    TypedAgent,
    Verdict,
    Verify,
)
from loop.shared.git import Commit, GitClient
from loop.shared.github.account import GitHub

from workflows.software_factory.agents.roles.chore import (
    CHORE,
    ChoreReply,
    ChoreRequest,
)
from workflows.software_factory.events.chore import ChoreDiscarded, ChoreRefined
from workflows.software_factory.events.specs import SpecRouted, SpecWork

DIFF_DIR = Path("bin") / "review_diff"
MANIFEST_NAME = "_manifest.tsv"
FALLBACK_SUMMARY = "refine the change set"
SUBJECT_LIMIT = 100

_DIFF_HEADER = "diff --git "
_SAME_PATH_HEADER = re.compile(r"^diff --git a/(?P<path>.+) b/(?P=path)$")
_RENAMED_HEADER = re.compile(r"^diff --git a/.+ b/(?P<path>.+)$")


@dataclass(frozen=True)
class ChoreInput:
    work: SpecWork
    routed: SpecRouted
    worktree: AgentWorkspace
    git: GitClient
    github: GitHub


@dataclass(frozen=True)
class _ChoreAgent(TypedAgent[ChoreRequest, ChoreReply]):
    """The chore agent as a best-effort step: any agent failure is a decline, and the captured diff never outlives the run."""

    def run(
        self,
        workspace: AgentWorkspace,
        request: ChoreRequest,
        *,
        session: str | None = None,
    ) -> ChoreReply:
        try:
            return super().run(workspace, request, session=session)
        except AgentError as exception:
            return Declined(str(exception))
        finally:
            shutil.rmtree(request.diff_dir, ignore_errors=True)


_AGENT = _ChoreAgent(CHORE.role, CHORE.profile, CHORE.encode, CHORE.decode)


def chore_subject(initiative: str, proposed: str) -> str:
    """`chore(<initiative>): ` + the first line of the agent's `proposed` message (or a fallback), without a repeated prefix, capped at 100 characters."""
    prefix = f"chore({initiative}): "
    lines = proposed.strip().splitlines()
    line = lines[0].strip() if lines else ""
    line = line.removeprefix(prefix).strip() or FALLBACK_SUMMARY
    return f"{prefix}{line}"[:SUBJECT_LIMIT].rstrip()


def refine(
    *,
    subject: Callable[[str, str], str] = chore_subject,
    verify: Verify | None = None,
) -> Stage[ChoreInput, ChoreReply, ChoreRefined]:
    """Best-effort chore agent on the feature worktree, skipped unless the target enables it; the runtime commits after `verify` with `subject(initiative, proposed message)`, and any failure restores the worktree and records the chore as discarded."""
    return (
        Stage(
            "refine-chore",
            ChoreInput,
            produces=ChoreRefined,
            may_emit=(ChoreDiscarded,),
        )
        .agent(_AGENT, request=_request)
        .gate(_refined)
        .contract(
            HarnessCommits(
                message=lambda i, event: _message(subject, i, event),
                verify=_discarding(verify),
                on_fail=Restore(),
            )
        )
        .when(_enabled)
    )


def capture_pull_request_diff(diff: str, worktree: Path) -> Path:
    """Writes one diff file per changed path, plus `_manifest.tsv` (`REAL_PATH<TAB>SANITIZED_FILENAME`), under `bin/review_diff` of `worktree`."""
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


def _enabled(i: ChoreInput) -> bool:
    """The target enables the chore and the feature branch has a pull request to refine."""
    return (
        i.routed.target.chore
        and i.github.open_pull_request(i.routed.target.slug, i.routed.feature_branch)
        is not None
    )


def _request(i: ChoreInput, path: Path) -> ChoreRequest:
    url = i.github.require_open_pull_request(
        i.routed.target.slug, i.routed.feature_branch
    ).url
    diff = i.github.pull_requests(i.routed.target.slug).diff(url)
    commits = tuple(
        _describe(commit) for commit in i.git.branch_commits(path, i.routed.base_branch)
    )
    return ChoreRequest(
        url,
        path,
        i.routed.base_branch,
        i.routed.feature_branch,
        capture_pull_request_diff(diff, path),
        commits,
    )


def _refined(
    i: ChoreInput, reply: ChoreReply, evidence: Evidence
) -> Verdict[ChoreRefined]:
    if isinstance(reply, Declined):
        return Fail(
            f"the chore agent failed: {reply.reason}", ChoreDiscarded(reply.reason)
        )
    if evidence.head != evidence.base:
        reason = f"the agent committed ({evidence.head}); only the runtime commits"
        return Fail(reason, ChoreDiscarded(reason, reply.out_of_scope))
    return Pass(
        ChoreRefined(
            reply.summary,
            reply.out_of_scope,
            reply.commit_message,
            committed=evidence.changed,
        )
    )


def _message(
    subject: Callable[[str, str], str], i: ChoreInput, event: ChoreRefined
) -> str:
    title = subject(i.routed.initiative, event.commit_message)
    return f"{title}\n\n{event.summary}" if event.summary else title


def _discarding(verify: Verify | None) -> Verify | None:
    """The verification, with its failure recorded as a discarded chore."""
    if verify is None:
        return None

    def check(path: Path) -> Fail[ChoreDiscarded] | None:
        failed = verify(path)
        return (
            None
            if failed is None
            else Fail(failed.reason, ChoreDiscarded(failed.reason))
        )

    return check


def _describe(commit: Commit) -> str:
    entry = f"{commit.sha[:7]} {commit.subject}"
    return f"{entry}\n{textwrap.indent(commit.body, '    ')}" if commit.body else entry


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
    return header.removeprefix(_DIFF_HEADER) if match is None else match["path"]


def _unique_name(name: str, directory: Path) -> str:
    # Flattening `/` to `_` can map two paths to one name; the manifest still tells them apart.
    candidate, counter = name, 1
    while (directory / candidate).exists() or candidate == MANIFEST_NAME:
        counter += 1
        candidate = f"{counter}_{name}"
    return candidate

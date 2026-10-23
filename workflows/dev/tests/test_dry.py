"""The dry-run slice end to end: scripted agent, in-memory Git and GitHub, observed through the library."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loop import AgentRunRecord, Role, WorktreeEvent
from workflows.dev import main
from workflows.dev.dry import DryGit, Fakes, commit_in_worktree
from workflows.dev.tests.doubles import FakePhaseSpan


class RecordingObserver:
    """Stands in for the OTLP observer: keeps what the library reports and the Workflow spans."""

    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint
        self.runs: list[AgentRunRecord] = []
        self.worktrees: list[str] = []
        self.spans: list[str] = []
        self.roles: dict[str, str | None] = {}

    def span(self, name: str, attributes: dict | None = None, *, role: str | None = None):
        spans = self.spans
        self.roles[name] = role

        class _Span(FakePhaseSpan):
            def __enter__(self):
                spans.append(name)
                return self

            def __exit__(self, *exc: object) -> None:
                return None

        return _Span()

    def agent_run(self, record: AgentRunRecord) -> None:
        self.runs.append(record)

    def worktree(self, event: WorktreeEvent) -> None:
        self.worktrees.append(event.point)


def _dry(tmp_path: Path, *extra: str) -> tuple[int, RecordingObserver]:
    observers: list[RecordingObserver] = []

    def new_observer(endpoint: str) -> RecordingObserver:
        observers.append(RecordingObserver(endpoint))
        return observers[0]

    code = main(["--dry", "--fake-speed", "0", "--log-dir", str(tmp_path), *extra], new_observer=new_observer)
    return code, observers[0]


def test_a_dry_run_delivers_every_ticket_and_publishes_the_runs_to_the_default_endpoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    code, observer = _dry(tmp_path)

    assert code == 0 and observer.endpoint == "http://localhost:4318"
    assert observer.worktrees == ["ready", "removing", "removed"]
    assert observer.spans[:2] == ["dev", "Spec"]
    assert observer.spans.count("Implement ticket") == 3
    assert "Refine changes" in observer.spans
    assert observer.spans.index("Review pull request") > observer.spans.index("Refine changes")


def test_workflow_spans_export_as_the_shared_roles(tmp_path: Path) -> None:
    _, observer = _dry(tmp_path)

    assert observer.roles["Spec"] == Role.ENGINEER
    assert {observer.roles[name] for name in ("Validate ticket commit", "Close ticket", "Publish draft PR")} == {Role.CODE}
    assert observer.roles["Implement ticket"] == Role.DEVELOPER
    assert observer.roles["Review pull request"] == Role.REVIEWER


def test_the_second_ticket_is_reworked_after_a_failed_first_attempt(tmp_path: Path) -> None:
    code, observer = _dry(tmp_path)

    outputs = [run.output or "" for run in observer.runs if run.agent == Role.DEVELOPER]
    assert code == 0 and len(outputs) == 3
    assert '"status": "failed"' in outputs[1] and '"status": "completed"' in outputs[2]


def test_the_fake_agent_reports_usage_and_tools_like_a_real_cli(tmp_path: Path) -> None:
    _, observer = _dry(tmp_path)

    record = observer.runs[0]
    assert record.usage is not None and record.usage.total_input_tokens > 0 and record.usage.total_aiu > 0
    assert [tool.name for tool in record.tools] == ["view", "edit", "bash"]
    assert record.exit_code == 0 and record.native_id is not None
    assert {run.agent for run in observer.runs} == {Role.DEVELOPER, "chore", Role.REVIEWER}


def test_no_rework_runs_each_ticket_once(tmp_path: Path) -> None:
    code, observer = _dry(tmp_path, "--no-rework")

    assert code == 0 and len([run for run in observer.runs if run.agent == Role.DEVELOPER]) == 2


def test_an_explicit_endpoint_wins_over_the_default(tmp_path: Path) -> None:
    _, observer = _dry(tmp_path, "--otlp-endpoint", "http://collector:4318")

    assert observer.endpoint == "http://collector:4318"


def test_dry_git_restore_drops_the_commits_after_a_sha() -> None:
    git = DryGit()
    keep = git.commit("one").__str__()
    git.commit("two")

    git.restore(Path("."), keep)

    assert [commit.subject for commit in git.commits] == ["base", "one"]


def test_each_fake_is_offered_only_for_its_own_seam() -> None:
    fakes = Fakes({"github", "store"})

    assert fakes.git() is None and fakes.new_agent() is None and fakes.readers() is None
    assert fakes.github_factory() is not None and fakes.store() is not None


def test_a_faked_git_without_a_faked_agent_is_refused() -> None:
    with pytest.raises(ValueError, match="faked agent"):
        Fakes({"git"})
    with pytest.raises(SystemExit):
        main(["--fake", "git"])


def test_an_unknown_seam_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown seam"):
        Fakes({"docker"})


def test_a_fake_agent_over_real_git_commits_in_the_worktree(tmp_path: Path) -> None:
    for args in (("init", "-q"), ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "base")):
        subprocess.run(("git", *args), cwd=tmp_path, check=True)

    sha = commit_in_worktree(tmp_path, "ccode(X|1): work")

    head = subprocess.run(("git", "log", "-1", "--format=%H %s"), cwd=tmp_path, capture_output=True, text=True, check=True)
    assert head.stdout.strip() == f"{sha} ccode(X|1): work"


def test_faking_only_the_agent_still_wires_the_fake_as_the_log_reader() -> None:
    fakes = Fakes({"agent"})

    assert fakes.new_agent() is not None and len(fakes.readers() or ()) == 1

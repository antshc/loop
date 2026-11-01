"""The generated babysit skill, its source synchronization, and PR supervision."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from loop import RunStatus

from scripts.babysit import supervise
from scripts.sync_babysit_skill import DESTINATION, synchronize

ROOT = Path(__file__).resolve().parents[4]
PR = "https://github.com/antshc/loop/pull/123"


def test_generated_skill_imports_without_the_factory_checkout(tmp_path: Path) -> None:
    """Bundled imports resolve when the caller has no factory source directory."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / DESTINATION)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "software_factory.workflows.pr_babysit",
            "--help",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--pr" in result.stdout


def test_skill_declares_loop_runtime_requirement() -> None:
    """The skill includes an installable Loop runtime dependency."""
    requirements = (ROOT / "skills/babysit/requirements.txt").read_text()

    assert "loop[otel] @ git+https://github.com/antshc/loop.git@main" in requirements


def test_sync_reads_the_index_not_unstaged_edits(tmp_path: Path) -> None:
    """The generated commit snapshot matches the index, even with later worktree edits."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    contents = {
        "scripts/babysit/start.py": "one\n",
        "scripts/babysit/supervise.py": "supervise\n",
        "software_factory/__init__.py": "",
        "software_factory/github.py": "",
        "software_factory/workflows/__init__.py": "",
        "software_factory/pull_requests/events/__init__.py": "event\n",
        "software_factory/workflows/pr_babysit/__main__.py": "workflow\n",
    }
    for name, content in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    synchronize(tmp_path, staged=True, check=False)
    assert synchronize(tmp_path, staged=True, check=True) == []
    generated = tmp_path / DESTINATION / "start.py"
    (tmp_path / "scripts/babysit/start.py").write_text("unstaged\n", encoding="utf-8")

    assert synchronize(tmp_path, staged=True, check=True) == []
    assert synchronize(tmp_path, staged=False, check=True) == [
        (DESTINATION / "start.py").as_posix()
    ]
    generated.write_text("manual edit\n", encoding="utf-8")
    assert synchronize(tmp_path, staged=True, check=True) == [
        (DESTINATION / "start.py").as_posix()
    ]
    synchronize(tmp_path, staged=True, check=False)
    assert generated.read_text(encoding="utf-8") == "one\n"

    subprocess.run(
        ["git", "add", "-A", "--", str(DESTINATION)], cwd=tmp_path, check=True
    )
    source = "software_factory/pull_requests/events/__init__.py"
    (tmp_path / source).unlink()
    subprocess.run(["git", "add", "-u", "--", source], cwd=tmp_path, check=True)
    synchronize(tmp_path, staged=True, check=False)
    assert not (
        tmp_path / DESTINATION / "software_factory/pull_requests/events/__init__.py"
    ).exists()


@pytest.mark.parametrize("url", ["", "https://example.com/a/b/pull/1", PR + "/files"])
def test_only_an_exact_github_pr_url_is_accepted(url: str) -> None:
    """Malformed or ambiguous PR targets are rejected before launching."""
    with pytest.raises(ValueError, match="Expected"):
        supervise.parse_pr(url)


def test_supervisor_watches_a_blocked_pr_until_it_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A handed-off workflow remains watched without repeating work immediately."""
    from loop.software_factory.infrastructure import ledger

    from software_factory.workflows.pr_babysit.settings import Settings

    settings = SimpleNamespace(
        state_dir=tmp_path / "state",
        worktrees_root=tmp_path / "worktrees",
        ledger_root=tmp_path / "state" / "ledger",
        interval=5,
        repository=lambda _: SimpleNamespace(path=tmp_path),
    )
    monkeypatch.setattr(Settings, "load", lambda _: settings)
    monkeypatch.setattr(supervise, "load_factory", lambda: None)
    monkeypatch.setattr(
        ledger,
        "Ledger",
        lambda _: SimpleNamespace(
            status=lambda *_: RunStatus(1, "handed_off", "clarification", {}, False)
        ),
    )
    states = iter(("OPEN", "OPEN", "OPEN", "CLOSED"))
    monkeypatch.setattr(supervise, "pr_state", lambda _: next(states))
    launched: list[list[str]] = []

    class Process:
        returncode = 1

        def poll(self) -> int:
            return self.returncode

    def spawn(command: list[str], **kwargs: object) -> Process:
        launched.append(command)
        return Process()

    monkeypatch.setattr(supervise.subprocess, "Popen", spawn)

    class FastStop(threading.Event):
        def __init__(self) -> None:
            super().__init__()
            self.waited: list[float] = []

        def wait(self, timeout: float | None = None) -> bool:
            self.waited.append(timeout or 0)
            return False

    stop = FastStop()
    ready = tmp_path / "ready"

    assert supervise.supervise(PR, tmp_path / "settings.json", ready, stop) == 0
    assert ready.exists()
    assert len(launched) == 1
    assert launched[0][-2:] == ["--pr", PR]
    assert stop.waited == [5]


def test_launcher_detaches_and_reports_only_after_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launcher returns a PID only after its supervisor confirms startup."""
    from software_factory.workflows.pr_babysit.settings import Settings

    monkeypatch.syspath_prepend(str(ROOT / "scripts" / "babysit"))
    start = importlib.import_module("start")
    settings = SimpleNamespace(
        state_dir=tmp_path / "state",
        repository=lambda _: SimpleNamespace(path=tmp_path),
    )
    monkeypatch.setattr(Settings, "load", lambda _: settings)
    monkeypatch.setattr(start, "load_factory", lambda: None)
    monkeypatch.setattr(start, "pr_state", lambda _: "OPEN")
    spawn_options: dict[str, object] = {}

    class Process:
        pid = 4321

        def poll(self) -> None:
            return None

    def spawn(command: list[str], **kwargs: object) -> Process:
        spawn_options.update(kwargs)
        Path(command[-1]).write_text("ready\n", encoding="utf-8")
        return Process()

    monkeypatch.setattr(start.subprocess, "Popen", spawn)

    pid, log = start.launch(PR, tmp_path / "settings.json")

    assert pid == 4321
    assert log.exists()
    assert log.with_name("pid").read_text(encoding="utf-8") == "4321\n"
    assert spawn_options["start_new_session"] is True
    assert spawn_options["cwd"] == settings.state_dir

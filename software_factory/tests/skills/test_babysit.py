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

ROOT = Path(__file__).resolve().parents[3]
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
        "software_factory/src/__init__.py": "",
        "software_factory/src/github.py": "",
        "software_factory/workflows/__init__.py": "",
        "software_factory/src/pull_requests/events/__init__.py": "event\n",
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
    source = "software_factory/src/pull_requests/events/__init__.py"
    (tmp_path / source).unlink()
    subprocess.run(["git", "add", "-u", "--", source], cwd=tmp_path, check=True)
    synchronize(tmp_path, staged=True, check=False)
    assert not (
        tmp_path / DESTINATION / "software_factory/src/pull_requests/events/__init__.py"
    ).exists()


def test_selective_sync_preserves_other_generated_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for path in (
        "scripts/babysit/supervise.py",
        "scripts/babysit/start.py",
        "software_factory/src/__init__.py",
        "software_factory/src/github.py",
        "software_factory/workflows/__init__.py",
    ):
        source = tmp_path / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(path, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    synchronize(tmp_path, staged=False, check=False)
    custom = tmp_path / DESTINATION / "start.py"
    custom.write_text("custom", encoding="utf-8")
    source = tmp_path / "scripts/babysit/supervise.py"
    source.write_text("fixed", encoding="utf-8")
    selected = (DESTINATION / "supervise.py",)

    assert synchronize(tmp_path, staged=False, check=True, only=selected) == [
        selected[0].as_posix()
    ]
    synchronize(tmp_path, staged=False, check=False, only=selected)

    assert synchronize(tmp_path, staged=False, check=True, only=selected) == []
    assert (tmp_path / selected[0]).read_text() == "fixed"
    assert custom.read_text() == "custom"
    with pytest.raises(ValueError, match="No canonical source"):
        synchronize(
            tmp_path, staged=False, check=False, only=(DESTINATION / "unknown.py",)
        )


def test_sync_applies_and_checks_bundle_overrides(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sources = {
        "scripts/babysit/start.py": "base\n",
        "scripts/babysit/bundle_overrides/start.py": "custom\n",
        "scripts/babysit/bundle_overrides/.deleted": "obsolete.py\n",
        "scripts/babysit/obsolete.py": "obsolete\n",
        "software_factory/src/__init__.py": "",
        "software_factory/src/github.py": "",
        "software_factory/workflows/__init__.py": "",
    }
    for name, content in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)

    synchronize(tmp_path, staged=True, check=False)
    assert synchronize(tmp_path, staged=True, check=True) == []
    assert (tmp_path / DESTINATION / "start.py").read_text() == "custom\n"
    assert not (tmp_path / DESTINATION / "obsolete.py").exists()
    (tmp_path / DESTINATION / "start.py").write_text("wrong\n")
    assert synchronize(tmp_path, staged=True, check=True) == [
        (DESTINATION / "start.py").as_posix()
    ]


@pytest.mark.parametrize("url", ["", "https://example.com/a/b/pull/1", PR + "/files"])
def test_only_an_exact_github_pr_url_is_accepted(url: str) -> None:
    """Malformed or ambiguous PR targets are rejected before launching."""
    with pytest.raises(ValueError, match="Expected"):
        supervise.parse_pr(url)


def test_missing_loop_reports_import_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(supervise, "bundle_path", lambda: ROOT / DESTINATION)
    for name in [name for name in sys.modules if name.startswith("loop")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "loop", None)

    with pytest.raises(
        ValueError, match=r"Install a compatible Loop.*ModuleNotFoundError"
    ):
        supervise.load_factory()


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
        repository=lambda _: SimpleNamespace(path=tmp_path, ignored_checks=()),
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
    states = iter(("OPEN", "OPEN", "CLOSED"))
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


@pytest.mark.parametrize("failure_at", [1, 2, 3])
@pytest.mark.parametrize("timeout", [False, True])
def test_supervisor_retries_github_outage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_at: int,
    timeout: bool,
) -> None:
    from loop.software_factory.infrastructure import ledger

    from software_factory.workflows.pr_babysit.settings import Settings

    settings = SimpleNamespace(
        state_dir=tmp_path / "state",
        worktrees_root=tmp_path / "worktrees",
        ledger_root=tmp_path / "state" / "ledger",
        interval=5,
        repository=lambda _: SimpleNamespace(path=tmp_path, ignored_checks=()),
    )
    monkeypatch.setattr(Settings, "load", lambda _: settings)
    monkeypatch.setattr(supervise, "load_factory", lambda: None)
    monkeypatch.setattr(
        ledger,
        "Ledger",
        lambda _: SimpleNamespace(
            status=lambda *_: RunStatus(1, "handed_off", "blocked", {}, False)
        ),
    )
    reads = 0
    launches: list[list[str]] = []
    waits: list[float] = []

    def read(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal reads
        reads += 1
        if failure_at <= reads < failure_at + 3:
            if timeout:
                raise subprocess.TimeoutExpired(command, 45)
            raise subprocess.CalledProcessError(
                1, command, stderr="error connecting to api.github.com"
            )
        return subprocess.CompletedProcess(
            command, 0, stdout="CLOSED" if reads > failure_at + 3 else "OPEN"
        )

    def spawn(command: list[str], **kwargs: object) -> SimpleNamespace:
        launches.append(command)
        return SimpleNamespace(poll=lambda: 0, returncode=0)

    def wait(seconds: float) -> bool:
        waits.append(seconds)
        return False

    stop = threading.Event()
    monkeypatch.setattr(supervise.subprocess, "run", read)
    monkeypatch.setattr(supervise.subprocess, "Popen", spawn)
    monkeypatch.setattr(stop, "wait", wait)

    assert (
        supervise.supervise(PR, tmp_path / "settings.json", tmp_path / "ready", stop)
        == 0
    )
    assert len(launches) == (2 if failure_at == 3 else 1)
    assert waits.count(5) >= 3
    assert "retrying" in caplog.text
    assert (
        "timed out" if timeout else "error connecting to api.github.com"
    ) in caplog.text


@pytest.mark.parametrize("detail", ["HTTP 401: Bad credentials", "HTTP 403: Forbidden"])
def test_supervisor_does_not_retry_access_refusals(
    monkeypatch: pytest.MonkeyPatch, detail: str
) -> None:
    def fail(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, command, stderr=detail)

    stop = threading.Event()
    monkeypatch.setattr(supervise.subprocess, "run", fail)
    monkeypatch.setattr(
        stop, "wait", lambda _: pytest.fail("access refusal was retried")
    )
    with pytest.raises(subprocess.CalledProcessError):
        supervise.wait_for_pr_state(PR, stop, 1)


def test_supervisor_stops_during_outage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            1, command, stderr="error connecting to api.github.com"
        )

    stop = threading.Event()

    def request_stop(seconds: float) -> bool:
        stop.set()
        return True

    monkeypatch.setattr(supervise.subprocess, "run", fail)
    monkeypatch.setattr(stop, "wait", request_stop)

    assert supervise.wait_for_pr_state(PR, stop, 1) is None
    assert "retrying" in caplog.text


def test_launcher_detaches_and_reports_only_after_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launcher returns a PID only after its supervisor confirms startup."""
    from software_factory.workflows.pr_babysit.settings import Settings

    monkeypatch.syspath_prepend(str(ROOT / "scripts" / "babysit"))
    start = importlib.import_module("start")
    settings = SimpleNamespace(
        state_dir=tmp_path / "state",
        repository=lambda _: SimpleNamespace(path=tmp_path, ignored_checks=()),
        otel_endpoint=None,
        aspire_dashboard_port=19000,
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

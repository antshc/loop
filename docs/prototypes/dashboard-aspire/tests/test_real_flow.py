from __future__ import annotations

from pathlib import Path

from conftest import Capture, per_call_log, write_log
from fakeloop.copilot_agent import CopilotAgent, CopilotOptions, ProcessResult
from fakeloop.runner import Run
from fakeloop.scenario import ScenarioOptions, run_scenario
from fakeloop.scratch_repo import ScratchRepo
import run_fake


class LoggingCli:
    """A fake `copilot` that only leaves a session log, like a real run does."""

    def __init__(self, home: Path) -> None:
        self.home, self.calls = home, 0

    def __call__(self, argv: list[str], cwd: Path, timeout: float) -> ProcessResult:
        self.calls += 1
        session_id = argv[argv.index("--session-id") + 1]
        write_log(self.home / "session-state" / session_id / "events.jsonl", per_call_log(session_id))
        return ProcessResult(0, "", "")


def test_real_dev_run_keeps_the_commit_phases_scripted(capture: Capture, tmp_path: Path) -> None:
    repo = ScratchRepo("feature/basket-discount", parent=tmp_path)
    cli = LoggingCli(tmp_path / "home")
    options = CopilotOptions(repo=repo.path, home=cli.home, run=cli)
    run = Run(capture.telemetry, "e", "dev", speed=0,
              agents=lambda config, prompt: CopilotAgent(config, prompt, options))

    run_scenario(run, ScenarioOptions("add a discount", rework=False, repo=repo))

    assert run.finish() == 0
    commits = [span for span in capture.spans.get_finished_spans() if span.name == "commit_develop_1"]
    subjects = [event.attributes["subject"] for span in commits for event in span.events]
    assert subjects == ["feat: add apply_discount", "feat: total honours the discount"]
    assert cli.calls == 5
    names = [span.name for span in capture.spans.get_finished_spans()]
    assert names.count("invoke_agent planner") == 1 and names.count("invoke_agent reviewer") == 2
    assert "fix_1" not in names
    repo.remove()


def test_real_run_without_the_copilot_binary_fails_before_any_telemetry(monkeypatch, capsys) -> None:
    monkeypatch.setattr(run_fake.shutil, "which", lambda name: None)

    assert run_fake.main(["--speed", "0"]) == 2
    assert "copilot is not on PATH" in capsys.readouterr().err


def test_real_agents_are_only_wired_for_the_dev_workflow(capsys) -> None:
    assert run_fake.main(["--workflow", "pr_babysitter"]) == 2
    assert "--dry" in capsys.readouterr().err


def test_dry_run_still_plays_the_scripted_workflow(capture: Capture, monkeypatch) -> None:
    monkeypatch.setattr(run_fake, "Telemetry", lambda: capture.telemetry)

    assert run_fake.main(["--dry", "--speed", "0"]) == 0

    names = [span.name for span in capture.spans.get_finished_spans()]
    assert "invoke_agent planner" in names and "fix_1" in names

"""Shared Workflow test-support: wires fakes at every process boundary around `dev.main`.

Imported by both the unit and integration test groups; holds no tests of its own.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from conftest import commit_file, git
from loop import CommandExecutor, CommandResult, InMemoryExecutionStore, NoSandbox
from loop.testing import FakeAgentClient, FakeCopilotCli, FakeGitClient
from workflows import dev
from workflows.platforms.work_tracking import GitHubClient
from workflows.platforms.work_tracking.fake_gh_cli import FakeGhCli


def _envelope(identifier: str = "Checkout|10", status: str = "completed", result: dict | None = None) -> str:
    return json.dumps({"identifier": identifier, "status": status, "result": result if result is not None else {}})


_COMPLETED = {"commit": "abc", "summary": "done", "verification": "ran tests"}


def _issue(number: int, title: str, *, state: str = "OPEN", labels: tuple[str, ...] = ()) -> dict:
    return {
        "number": number,
        "title": title,
        "url": f"https://github.com/owner/repo/issues/{number}",
        "state": state,
        "body": f"Body of #{number}",
        "labels": {"nodes": [{"name": label} for label in labels]},
        "comments": {"nodes": []},
    }


def _make_repo(path: Path, origin: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    commit_file(path, "README.md", "hello\n", "initial")
    git(path, "remote", "add", "origin", origin)
    return path


def _commit_and_report(
    harness: "DevHarness",
    number: int,
    *,
    initiative: str = "Checkout",
    summary: str = "done",
    subject: str | None = None,
) -> str:
    commit = harness.git.commit(_only_worktree(harness.git), subject or f"ccode({initiative}|{number}): work")
    return _envelope(
        f"{initiative}|{number}", result={"commit": commit, "summary": summary, "verification": "ran tests"}
    )


class DevHarness:
    """Wires `dev.main` to fakes at every process boundary; the harness is a real repo with a github.com origin."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        specs: list[dict] | None = None,
        tickets: dict[int, list[dict]] | None = None,
        prs: list[dict] | None = None,
    ) -> None:
        self.tmp_path = tmp_path
        self.harness_root = _make_repo(tmp_path / "harness", "git@github.com:owner/repo.git")
        self.log_dir = tmp_path / "logs"
        self.git = FakeGitClient()
        self.git.remote_branches.add("main")
        self.gh = FakeGhCli(
            specs=specs
            if specs is not None
            else [_issue(1, "Checkout: Add login page", labels=("spec", "repo:target:owner/repo", "repo:base:main"))],
            tickets=tickets if tickets is not None else {1: [_issue(10, "Add login form")]},
            prs=prs,
        )
        self.github = GitHubClient("owner", "repo", gh=self.gh)
        self.store = InMemoryExecutionStore()
        self.agent_calls: list[tuple[str, object]] = []

    def run(
        self,
        *,
        handler=None,
        agent_factory=None,
        sandbox_factory=None,
        github_factory=None,
        git=None,
        store=None,
        hooks=(),
        log_dir: Path | None = None,
        cancel: threading.Event | None = None,
    ) -> int:
        git_client = git if git is not None else self.git

        def tracking_handler(prompt, options):
            self.agent_calls.append((prompt, options))
            return (handler or (lambda prompt, options: ""))(prompt, options)

        def default_agent_factory(executor):
            return FakeAgentClient(tracking_handler)

        return dev.main(
            ["--harness-root", str(self.harness_root), "--log-dir", str(log_dir or self.log_dir)],
            git=git_client,
            branches=git_client.branch_service,
            commits=git_client.commits,
            github_factory=github_factory or (lambda checkout: self.github),
            agent_factory=agent_factory or default_agent_factory,
            sandbox_factory=sandbox_factory
            or (lambda workspace, cancel: NoSandbox(workspace, executor=FakeCopilotCli(), cancel=cancel)),
            store=store if store is not None else self.store,
            hooks=hooks,
            cancel=cancel,
        )


def _only_worktree(git_client: FakeGitClient) -> Path:
    return next(iter(git_client.worktrees))


def _writes(gh: FakeGhCli) -> list[tuple[str, ...]]:
    return [call for call in gh.calls if call[0] in ("issue", "pr")]


def _commit_then(harness: "DevHarness", response) -> object:
    def handler(prompt, options):
        commit = harness.git.commit(_only_worktree(harness.git), "ccode(Checkout|10): work")
        return response(commit) if callable(response) else response

    return handler


def _completed(commit: str, **result: str) -> str:
    return _envelope(result={"commit": commit, "summary": "done", "verification": "ran tests", **result})


def _event(event_type: str, data: dict | None = None) -> str:
    frame: dict = {"type": event_type}
    if data is not None:
        frame["data"] = data
    return json.dumps(frame)


def copilot_event_frames(identifier: str, status: str, result: dict | None = None) -> list[str]:
    """JSON event lines shaped like a real `copilot -p --output-format json` run.

    Modeled on docs/experiments/copilot-cli-json-output-events.md (a design source, never imported): unrelated
    session/turn events, the response envelope split across many assistant.message_delta events, the closing
    assistant.message, and the final result event.
    """
    envelope = json.dumps({"identifier": identifier, "status": status, "result": result if result is not None else {}})
    chunk = 6
    deltas = [envelope[index : index + chunk] for index in range(0, len(envelope), chunk)]
    return [
        _event("session.mcp_server_status_changed"),
        _event("session.mcp_server_status_changed"),
        _event("session.extensions_loaded"),
        _event("session.tools_updated"),
        _event("session.mcp_servers_loaded"),
        _event("user.message"),
        _event("assistant.turn_start"),
        _event("model.call_start"),
        _event("assistant.message_start"),
        *[_event("assistant.message_delta", {"messageId": "m1", "deltaContent": part}) for part in deltas],
        _event("model.call_finished"),
        _event("assistant.message", {"content": envelope}),
        _event("model.call_final_result"),
        _event("assistant.turn_end"),
        _event("session.usage_checkpoint"),
        _event("assistant.idle"),
        '{"type": "result", "sessionId": "fake-session", "exitCode": 0}',
    ]


def copilot_event_frames_with_leading_noise(
    identifier: str, status: str, result: dict | None = None, *, noise_identifier: str = "Other|1"
) -> list[str]:
    """`copilot_event_frames`, preceded by an unrelated completed envelope the parser must look past."""
    noise = json.dumps({"identifier": noise_identifier, "status": "completed", "result": {}})
    return [
        _event("assistant.message_delta", {"messageId": "noise", "deltaContent": noise}),
        *copilot_event_frames(identifier, status, result),
    ]


def copilot_event_frames_without_response() -> list[str]:
    """Assistant prose with no `{identifier, status}` object anywhere in the output."""
    return [
        _event("session.mcp_server_status_changed"),
        _event("user.message"),
        _event("assistant.turn_start"),
        _event("assistant.message_delta", {"messageId": "m1", "deltaContent": "Still working through this, "}),
        _event("assistant.message_delta", {"messageId": "m1", "deltaContent": "no final answer yet."}),
        _event("assistant.message", {"content": "Still working through this, no final answer yet."}),
        _event("assistant.turn_end"),
        '{"type": "result", "sessionId": "fake-session", "exitCode": 0}',
    ]


def copilot_event_frames_with_malformed_response(identifier: str, status: str, result: dict | None = None) -> list[str]:
    """The response JSON cut off mid-object, so no balanced top-level object can be parsed from it."""
    envelope = json.dumps({"identifier": identifier, "status": status, "result": result if result is not None else {}})
    truncated = envelope[: len(envelope) // 2]
    return [
        _event("assistant.turn_start"),
        _event("assistant.message_delta", {"messageId": "m1", "deltaContent": truncated}),
        _event("assistant.message", {"content": truncated}),
        _event("assistant.turn_end"),
        '{"type": "result", "sessionId": "fake-session", "exitCode": 0}',
    ]


@dataclass
class RecordingExecutor:
    """Wraps a CommandExecutor, recording every line delivered to on_line and whether the run ended early."""

    inner: CommandExecutor
    lines: list[str] = field(default_factory=list)
    terminated: bool = False

    def __call__(
        self,
        command: Sequence[str] | str,
        *,
        timeout_s: float | None = None,
        on_line: Callable[[str], "bool | None"] | None = None,
        cancel: threading.Event | None = None,
    ) -> CommandResult:
        def record(line: str) -> bool | None:
            self.lines.append(line)
            stop = on_line(line) if on_line is not None else None
            if stop or (cancel is not None and cancel.is_set()):
                self.terminated = True
            return stop

        return self.inner(command, timeout_s=timeout_s, on_line=record, cancel=cancel)

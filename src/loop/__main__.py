"""Dry-run demo: `python -m agent --dry-run` from the prototype directory."""

from __future__ import annotations

import sys
from pathlib import Path

from . import (
    Agent,
    AgentOptions,
    AgentProfile,
    AgentRequest,
    BranchStrategy,
    CodexCli,
    GitOptions,
    MergeToHeadStrategy,
    SessionEndAgentCliHook,
    SessionName,
    SessionStartAgentCliHook,
    WorktreeReadyLoopHook,
    copilot,
)

# Run from the harness dir (the agent cwd). `--dry-run` only logs; otherwise real git runs in workspace/repo1 and repo2.
if "--dry-run" in sys.argv:
    _DRY = AgentOptions(dry_run=True)
    _root = Path("workspace/repo1.worktrees")
    _repo = Path("workspace/repo1")
    # One-shot: one git lifecycle per run().
    Agent(_DRY).with_git(GitOptions(_root, _repo, MergeToHeadStrategy())).create().run(AgentRequest("Implement ticket #123 in repo1"))
    # Role profiles; which CLI and model fill a role is a workflow choice.
    PLANNER = AgentProfile(copilot, "claude-opus-4.5", "high")
    DEVELOPER = AgentProfile(CodexCli(hook_trust_bypass=True), "gpt-5-codex", "high")
    REVIEWER = AgentProfile(copilot, "claude-sonnet-4.5")
    # Shared worktree: one git lifecycle, several CLIs; one named session, kept per CLI.
    _loop_hooks = (
        WorktreeReadyLoopHook('cp "$LOOP_REPOSITORY/.env" .env'),
        WorktreeReadyLoopHook("npm ci", timeout_sec=600),
    )
    _shared = Agent(_DRY).with_git(
        GitOptions(_root, _repo, BranchStrategy("loop/ticket-123", "main"), loop_hooks=_loop_hooks)
    ).with_session()
    _shared.with_agent_cli_hooks(
        SessionStartAgentCliHook("./scripts/session-start.sh"),
        SessionEndAgentCliHook("notify-send", timeout_sec=3),
    )
    with _shared.open(session=SessionName("ticket-123")) as _wt:
        _plan = _wt.agent(PLANNER).run(AgentRequest("Plan ticket #123 in repo1"))
        _developer = _wt.agent(DEVELOPER)
        _developer.run(AgentRequest(f"Implement this plan:\n{_plan.output}"))
        _developer.run(AgentRequest("Fix failing tests"))  # resumes the Codex session
        # A fresh session gives an unbiased review.
        _wt.agent(REVIEWER, session=SessionName.new()).run(AgentRequest("Review the diff against the plan"))
    # Without with_session(), each run starts under a new `loop-<hex>` name.
    _fresh = Agent(_DRY).with_git(GitOptions(_root, _repo, BranchStrategy("loop/ticket-123", "main"))).create()
    _fresh.run(AgentRequest("Plan ticket #123 in repo1"))
    _fresh.run(AgentRequest("Implement ticket #123 in repo1"))
    sys.exit()
# print(repo_agent("repo1", "loop/ticket-123").run("Implement ticket #123 in repo1"))
# print(repo_agent("repo2", "loop/ticket-123").run("Implement ticket #123 in repo2"))
# Named session: the second run resumes the first (the client is reused).
# _agent = repo_agent("repo1", "loop/ticket-123", session=SessionName("ticket-123"))
# _agent.run(AgentRequest("Plan ticket #123 in repo1"))
# _agent.run(AgentRequest("Implement the plan"))

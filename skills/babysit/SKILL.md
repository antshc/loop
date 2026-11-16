---
description: Launch a background PR babysitter for a GitHub pull request URL, continuously monitor review feedback and CI, recover merge conflicts, and stop when the PR closes or merges.
name: babysit
---
# Babysit a pull request

1. Take one GitHub PR URL (`https://github.com/owner/repo/pull/number`). Do not substitute a repository scan.
2. Require Python 3.14+ and install dependencies with `python3 -m pip install -r <skill-directory>/requirements.txt` in the Python environment used to launch the skill.
3. Locate the local babysit JSON config: `--config PATH`, `LOOP_BABYSIT_CONFIG`, or `.loop/pr-babysit.json` in the caller's repository. If absent, request configuration; do not guess build or test commands. Settings reference: [README.md](README.md).
4. Run `python3 <skill-directory>/scripts/start.py PR_URL [--config ABSOLUTE_PATH]` while preserving the caller's repository as the working directory. The script resolves the generated `software_factory` package relative to its own file and launches one detached supervisor.
5. Return the process ID and log path **only after startup is confirmed**. Do not wait in the agent session.
6. The supervisor uses the `pr_babysit` workflow, the existing ledger and recovery semantics. A blocked PR remains monitored, but unchanged blocked work must not be repeated.
7. Review threads are handled by the `address` skill logic: each poll runs the copied address gate (`discussion_state.py`); `proceed` with a discussion state no earlier run left triggers the address agent in babysit's PR worktree; the gate's JSON is saved to `$TMPDIR/babysit-discussions/` and the agent gets its path, not the JSON, in the prompt. The agent follows the address procedure and commits, pushes, replies and resolves settled agent threads itself; human/unknown threads keep manual closure.
8. Draft PRs are only watched. Once marked ready, babysit repairs conflicts, CI and review threads, including threads left while it was a draft. Jenkins runs only when `jenkins.enabled` is `true`; otherwise skipped.
9. The PR worktree persists across polls and runs, fast-forwarded to origin on each use; it is removed only when the PR is merged or closed (a dirty one is kept). A merged or closed PR ends the process. SIGTERM requests graceful shutdown. A configuration or unrecoverable error stops with a log; never claim continued monitoring after an error.

## Gotchas

- Install the `loop[otel]` Python package from `requirements.txt`; the skill does not vendor the Loop library.
- With a local `otel_endpoint`, `start.py` starts the Aspire dashboard (`aspire` CLI required) if nothing listens there; report its URL (`http://localhost:<aspire_dashboard_port>`, default `19000`) with the PID and log. Babysitters share it; it outlives them.
- The repository checkout, authenticated `gh`, agent CLI, build/test commands and optional Jenkins credentials are configured by the existing workflow settings.
- `scripts/` started as Loop-generated code but now diverges locally (address gate + address agent). Do not regenerate from Loop with `sync_babysit_skill.py`; it would drop these changes.
- The address gate and agent instructions are copied from the `address` skill (`scripts/pr_discussion_state.py`, `SKILL.md`, `references/`) and the `review-conversation` skill. Update the copies under `scripts/software_factory/src/pull_requests/{infrastructure/github/pull_requests/discussion_state.py,agents/roles/address/}` when those skills change.
- Only unresolved review threads are addressed; PR conversation comments and review bodies are ignored. Replies must carry the hidden `<!-- agent:reply` block, or the gate treats them as new work.
- A second invocation targets a separate process; per-PR locking prevents simultaneous modifications to the same PR.

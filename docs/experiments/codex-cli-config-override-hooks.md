# Experiment: Codex `-c hooks.<Event>=[…]` overrides in `codex exec`

Answers "Further Considerations" item 4 of [agent-cli-hooks-prototype.md](../plans/agent-cli-hooks-prototype.md). Run 2026-10-10.

## Question

Does `codex exec` accept hooks declared through repeated `-c hooks.<Event>=[{matcher=…,hooks=[{type="command",command=…,timeout=…}]}]` overrides, and do they fire without a `hooks.json` file? Does `--dangerously-bypass-hook-trust` decide whether they run?

Out of scope: Copilot hooks, `exec resume`, hook-trust persistence (item 6), Docker.

## Method

- **CLI under test:** `codex-cli 0.162.1` on `PATH`; `codex features list` reports `hooks stable true`. Model `gpt-5.5` (the default `gpt-5.3-codex` is unsupported for this ChatGPT account and fails every run before any hook can matter).
- **Scratch:** `/tmp/loop-codex-hook-exp` (`git init`). `hook.sh` appends stdin and a count of `CODEX_*` env vars to `hook.log`. No files in the repo were touched.
- **Common flags:** `codex exec --skip-git-repo-check --ephemeral -m gpt-5.5`, prompt `Reply with the single word OK`.
- **Hook override shape (one `-c` per event):** `-c 'hooks.SessionStart=[{matcher="startup|resume",hooks=[{type="command",command="/tmp/loop-codex-hook-exp/hook.sh",timeout=5}]}]'`. Same form, without `matcher`, for `SessionEnd` (`timeout=3`) and `Stop`.
- **Cases:**
  - A: three overrides plus `--dangerously-bypass-hook-trust`.
  - B: same overrides, no bypass flag.
  - C: malformed override `-c 'hooks.SessionStart=[{matcher='`.
  - D: A plus `UserPromptSubmit`, `PreToolUse`, `PostToolUse` overrides, prompt asks to run `echo hi`.

## Observations

- **A:** exit 0, output `OK`. `hook.log` received `SessionStart`, `Stop`, `SessionEnd` payloads. stderr showed `hook: SessionStart` / `hook: SessionStart Completed` and `hook: Stop` / `hook: Stop Completed`.
- **B:** exit 0, output `OK`, same overrides. **No hook ran**: no `hook:` lines on stderr, no `hook.log`. No error or warning about untrusted hooks.
- **C:** exit 1, `Error loading config.toml: invalid type: string "[{matcher=", expected a sequence in hooks`. Malformed values are rejected, not ignored.
- **D:** exit 0, output `DONE`. Events in order: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SessionEnd`.
- Payload keys (snake_case, JSON on stdin):
  - `SessionStart`: `session_id, transcript_path, cwd, hook_event_name, model, permission_mode, source` (`source` was `startup`).
  - `SessionEnd`: `session_id, transcript_path, cwd, hook_event_name, reason` (`reason` was `other`).
  - `PreToolUse`: adds `turn_id, tool_name` (`Bash`), `tool_input` (`{"command":"echo hi"}`), `tool_use_id`.
- `transcript_path` was `null` (`--ephemeral`).
- The hook process saw zero `CODEX_*` env vars, so correlation cannot rely on them; bake the session name into the command.
- In D, `permission_mode` was `bypassPermissions`. Not verified whether that comes from `--dangerously-bypass-hook-trust` or from the flags used in that run.

## Conclusion

- **Yes:** `codex exec` accepts `-c hooks.<Event>=[{matcher=…,hooks=[{type="command",…}]}]` and fires the hooks with no `hooks.json`.
- Without `--dangerously-bypass-hook-trust` the hooks are **silently skipped**, with exit code 0. This confirms the plan's decision: `CodexCli.hook_points` must be empty unless `hook_trust_bypass=True`, otherwise hooks silently never run.
- The plan's Codex event names (`SessionStart`, `SessionEnd`, `Stop`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`) are valid and fire as expected. `SubagentStart`, `SubagentStop`, `PreCompact` were not exercised.
- The `SessionEnd` payload uses `reason`, and the tool payload uses `tool_name`, matching the shim's snake_case mapping. The shim must also read `source` from `SessionStart` only.
- Implication: `CodexCli.hook_wiring` can emit `-c` args as planned. Still unverified: the 3-second `SessionEnd` clamp (a 3 s timeout was accepted) and `exec resume` argv.

## Evidence

- Case A stderr: `hook: SessionStart`, `hook: SessionStart Completed`, `hook: Stop`, `hook: Stop Completed`; `hook.log` contained three payloads.
- Case B stderr: zero `hook:` lines; no `hook.log`.
- Case C stderr: `invalid type: string "[{matcher=", expected a sequence in hooks`.
- Case D `hook_event_name` sequence as listed above.

## Cleanup

`/tmp/loop-codex-hook-exp` removed. No repo files other than this note changed.

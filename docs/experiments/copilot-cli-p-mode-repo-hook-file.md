# Experiment: Copilot `-p` loads a runner-written `.github/hooks/loop-<hex>.json`

Answers "Further Considerations" item 5 of [agent-cli-hooks-prototype.md](../plans/agent-cli-hooks-prototype.md) and the `.github/hooks/loop-<hex>.json` row in [copilot-cli-hooks.md](../research/copilot-cli-hooks.md). Run 2026-10-10.

## Question

If the runner writes `.github/hooks/loop-<hex>.json` into a git worktree before `copilot -p`, does Copilot load it and fire the hooks, leave the file unmodified, and stop firing once the runner deletes it?

Also: is the file still loaded when excluded through `.git/info/exclude` (the plan's `git_excludes`), when `copilot` starts in a subdirectory, when two `loop-*.json` files coexist, and can inline `hooks` in `.github/copilot/settings.local.json` replace the file.

Out of scope: `--resume`, `subagent*`/`preCompact` events, hook output steering, Docker.

## Method

- **CLI under test:** GitHub Copilot CLI 1.0.95.
- **Scratch:** `/tmp/loop-cop-hook-exp`: `git init main` with an empty commit, plus `git worktree add ../wt -b exp`. Runs started from `wt`, the folder was never trusted interactively. No repo files were touched.
- **Hook file:** `wt/.github/hooks/loop-<uuid4 hex>.json`, `{"version":1,"hooks":{<event>:[{"type":"command","bash":"/tmp/loop-cop-hook-exp/hook.sh <event>","timeoutSec":10}]}}` for `sessionStart`, `sessionEnd`, `agentStop`, `userPromptSubmitted`, `preToolUse`, `postToolUse`. `hook.sh` appends event name, `$PWD`, stdin and the names of `COPILOT*`/`LOOP*` env vars to `hook.log`, and exits 0.
- **Common command:** `copilot -p 'Run `echo hi` in the shell, then reply DONE' --allow-tool='shell(echo)' </dev/null`.
- **Cases:**
  - A: no opt-in env vars.
  - B: `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=true`.
  - C: `COPILOT_ALLOW_ALL=true`.
  - D: hook file and `.github/` deleted, B's env var set, prompt `Reply DONE`.
- After A, B and C the file's sha256 was compared with the value taken at creation.
- **Extension cases** (fresh scratch, same `hook.sh` extended to log a tag, `cwd` from the payload and `COPILOT_PROJECT_DIR`; B's env var set unless stated; hooks `sessionStart`, `preToolUse`, `sessionEnd`):
  - E: `loop-aaa.json` excluded by `.github/hooks/loop-*.json` in the worktree's `git rev-parse --git-path info/exclude`; `git check-ignore` confirmed it is ignored; `copilot` started at the worktree root.
  - F: same file, `copilot` started in `wt/sub`.
  - H: E's file plus a second `loop-bbb.json` (different tag).
  - G: no hook files; the same hooks as an inline `hooks` block in `.github/copilot/settings.local.json`.
  - G2: G without the env var.

## Observations

- **A:** exit 0, output `DONE`, the tool ran. **No hook fired**, no `hook.log`, no warning on stderr. File unchanged.
- **B:** exit 0. All six hooks fired in order `userPromptSubmitted`, `sessionStart`, `preToolUse`, `postToolUse`, `agentStop`, `sessionEnd`. File unchanged.
- **C:** same six events in the same order as B. File unchanged.
- **D:** exit 0, output `DONE`, **no hook fired**, `hook.log` absent. After the file was removed, `wt` held only `.git`, so Copilot left nothing behind (no `.github/`, no untracked files).
- Hook process facts (B, C):
  - `$PWD` and payload `cwd` were the worktree root.
  - Env had `COPILOT_CLI`, `COPILOT_AGENT`, `COPILOT_PROJECT_DIR`, `COPILOT_CLI_BINARY_VERSION`, `COPILOT_CLI_RESOLVED_DIST_DIR`, `COPILOT_DEBUG_NONCE`; no session-id variable.
- Payloads (camelCase, JSON on stdin):
  - `userPromptSubmitted`: `sessionId, timestamp, cwd, prompt`.
  - `sessionStart`: `source` was `new`, plus `initialPrompt`.
  - `preToolUse`/`postToolUse`: `toolName` `bash`, **`toolArgs` is a JSON object** (`{"command":"echo hi","description":"Run echo hi"}`), not a JSON string. `postToolUse` adds `toolResult {resultType, textResultForLlm}`.
  - `agentStop`: `transcriptPath, stopReason` (`end_turn`), `stop_hook_active`.
  - `sessionEnd`: `reason` was `complete`.
- Timing: `agentStop` and `sessionEnd` were ~13 ms apart, `sessionEnd` last.
- **E:** all three hooks fired although the file is git-ignored. `git rev-parse --git-path info/exclude` from the worktree resolved to the **main** repo's `.git/info/exclude`, so the exclude line is shared by every worktree.
- **F:** all three hooks fired from `wt/sub`. Payload `cwd` was `wt/sub`, but the hook process `$PWD` and `COPILOT_PROJECT_DIR` were `wt` (the project root).
- **H:** both files loaded; each event ran once per file, no deduplication.
- **G:** inline hooks in `settings.local.json` fired (all three events). **G2:** without the env var, nothing fired and exit was 0, so inline repo settings are gated like hook files.
- Copilot did not write or change any file in the worktree in E-H (`git status` showed only the files the experiment created).

## Conclusion

- **Yes, with an opt-in:** `copilot -p` loads `.github/hooks/loop-<hex>.json` from a never-trusted worktree only when `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=true` or `COPILOT_ALLOW_ALL=true` is set. Without either, hooks are **silently skipped** with exit 0, so `CopilotCli` must set the env var (it is narrower than `COPILOT_ALLOW_ALL`) or hooks silently never run.
- Copilot does not modify, rename or recreate the file; deleting it after the run is sufficient and the next run has no hooks. Nothing else is written into the worktree.
- `sessionEnd` fires in `-p`, once, with `reason: complete`.
- **Contradicts the plan/research:**
  - `userPromptSubmitted` **does** fire in `-p`, so `PROMPT_SUBMITTED` need not be excluded from `CopilotCli.hook_points` (the plan's reason covered only `type: "prompt"` hooks).
  - `toolArgs` is an object, not a string; the shim's `normalise` must not `json.loads` it, and the research doc's `jq -r '.toolArgs' | grep` guard example is shaped for a string.
- Hook `cwd` resolves to the project root, not the session directory: started from `wt/sub`, the hook ran in `wt` while the payload `cwd` stayed `wt/sub`. This supports 1.0.88 over 1.0.72 for config-file hooks; set `cwd` on the entry if a hook needs the agent's directory.
- `git_excludes` is safe: an excluded file is still loaded. The exclude path from `git rev-parse --git-path info/exclude` is the main repo's file, shared across worktrees, so one line covers all and repeated appends must stay idempotent.
- Several `loop-*.json` files in one worktree all load and each fires; concurrent runs sharing a worktree would see each other's hooks, so keep one run per worktree and delete the file afterwards.
- Inline `hooks` in `.github/copilot/settings.local.json` also work and share the same env-var gate, but are a single shared file; per-run `loop-<hex>.json` files avoid read-modify-write races, so the plan's file approach stands.
- Session correlation: no session-id env var reaches hooks, so keep baking `--session <name>` into the command; `sessionId` in the payload is Copilot's UUID, not the runner's name.
- Copilot CLI 1.0.95 has no hook argument (`copilot help`), so a file or settings block is the only route; Codex's `-c hooks.<Event>=[…]` overrides fire hooks without writing any hooks file (checked separately; see [codex-cli-config-override-hooks.md](codex-cli-config-override-hooks.md)).
- Still unverified: `subagent*`/`preCompact` events, `--resume` runs, and session-state files left under `~/.copilot/session-state/`.

## Evidence

- A: `ls ../hook.log` absent; `sha256sum -c`: OK.
- B and C: `hook.log` held six blocks each, order as above; `sha256sum -c`: OK.
- D: `ls -A` printed `.git` only; no `hook.log`.
- E: `hook.log` held `sessionStart`, `preToolUse`, `sessionEnd` with tag `E`; `git check-ignore -v` printed the `info/exclude:7` rule.
- F: `cwd: /tmp/loop-cop-hook-exp/wt/sub` in payloads, `pwd=/tmp/loop-cop-hook-exp/wt`, `COPILOT_PROJECT_DIR=/tmp/loop-cop-hook-exp/wt`.
- H: six blocks, tags `E` and `H`, one per event each.
- G: three blocks tagged `G`; G2: no `hook.log`.

## Cleanup

`/tmp/loop-cop-hook-exp` removed. Only this note was added to the repo. Copilot session state for the four runs remains under `~/.copilot/session-state/`.

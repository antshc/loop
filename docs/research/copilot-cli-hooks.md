# Copilot CLI hooks

Hook events, configuration and usage for GitHub Copilot CLI 1.0.95, as of 2026-10-10. Primary sources: GitHub Docs hooks pages, the `copilot-cli` changelog, and the installed CLI's `copilot help`.

**Bottom line.** Copilot CLI runs external commands (or HTTP calls) at 14 lifecycle events, configured as `version: 1` JSON under `.github/hooks/*.json` (repo), `~/.copilot/hooks/*.json` (user), or `hooks` blocks in settings files. Only `preToolUse`, `permissionRequest`, `agentStop`, `subagentStop`, `postToolUse` and a few context-injecting events can steer the agent; the rest only observe. In `-p` mode, repo hooks do not load unless the folder is trusted or `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=true` is set, and `sessionEnd` fires once per completed turn instead of once per process ([changelog]). The prototype plan needs to account for both.

## Events

Source: [hooks-ref] "Hook events". Names are camelCase; PascalCase names select the VS Code/Claude snake_case payload format.

| Event | Fires when | Can steer? |
|---|---|---|
| `sessionStart` | New or resumed session begins | `additionalContext` only |
| `sessionEnd` | Session terminates | No |
| `userPromptSubmitted` | User submits a prompt | Command/HTTP hooks: output dropped; `modifiedPrompt` only for SDK hooks |
| `userPromptTransformed` | After the runtime builds the model-facing prompt | `modifiedTransformedPrompt` |
| `preToolUse` | Before each tool runs | allow / deny / ask, `modifiedArgs` |
| `postToolUse` | After a tool succeeds | `modifiedResult`, `additionalContext` |
| `postToolUseFailure` | After a tool fails | `additionalContext` (exit 2) |
| `permissionRequest` | Before the permission service runs | allow / deny, `interrupt` |
| `agentStop` | Main agent finishes a turn | `decision: "block"` forces another turn |
| `subagentStart` | Subagent spawned | `additionalContext` prepended to its prompt |
| `subagentStop` | Subagent completes | block, `modifiedResponse` |
| `preCompact` | Context compaction about to start | No |
| `errorOccurred` | Error during execution | No |
| `notification` | CLI emits a system notification (async, fire-and-forget) | `additionalContext` |

Notes:

- The built-in `general-purpose` agent emits no `subagentStart`/`subagentStop` ([hooks-ref] "subagentStart").
- `preMcpToolCall` appears in the changelog (1.0.51) as a hook "for hook providers"; the reference page does not list it. **UNVERIFIED** for config-file hooks ([changelog]).
- `about-hooks` lists only 8 types and is older than the reference ([about-hooks]).

## Hook entry types

| `type` | Fields | Notes |
|---|---|---|
| `command` (default) | `bash`, `powershell`, or `command` (cross-platform alias); `cwd`, `env`, `timeoutSec` (alias `timeout`, default 30) | CLI also accepts `exec` + `args` to run an executable without a shell. Do not combine `exec` with `bash`/`powershell`/`command`. |
| `http` | `url`, `headers`, `allowedEnvVars`, `timeoutSec` | `https://` only; `http://localhost` needs `COPILOT_HOOK_ALLOW_LOCALHOST=1`. `preToolUse`/`permissionRequest` must use `https://`. |
| `prompt` | `prompt` | `sessionStart` only. CLI: new interactive sessions only; does not fire on resume or in `-p`. |

Optional `matcher` regex (compiled as `^(?:PATTERN)$`, full match) on `notification` (`notification_type`), `permissionRequest`/`preToolUse`/`postToolUse` (`toolName`), `preCompact` (`trigger`), `subagentStart` (`agentName`). An invalid regex skips the hook ([hooks-ref] "Matcher filtering").

Source for all of the above: [hooks-ref] "Hook configuration format".

## Where hooks load from

Sources are combined in this order, and every entry for an event runs ([hooks-ref] "Hooks locations"):

1. Policy: `/etc/github-copilot/policy.d/*.json` (Linux/macOS, root-owned, not group/world-writable). Not disabled by `disableAllHooks`.
2. User: `~/.copilot/hooks/*.json`, or `$COPILOT_HOME/hooks/*.json` when `COPILOT_HOME` is set. Also the `hooks` field of `~/.copilot/settings.json`; `config.json` is no longer read for hooks.
3. Project: `.github/hooks/*.json` in the repo root, and the `hooks` field in `.github/copilot/settings.json` / `settings.local.json`. `.claude/settings.json` and `.claude/settings.local.json` are also read.
4. Plugins: each plugin's `hooks.json`.

File format: `{ "version": 1, "hooks": { "<event>": [ <entry>, ... ] } }`. A malformed item in a directory-loaded file is dropped and its siblings still load; invalid JSON, bad `version`, or a non-array event list rejects the whole file. Inline settings hooks are strict ([hooks-ref]). Config is read at startup ([hooks-howto]).

`disableAllHooks: true` in a hook file skips that file; at the top level of repo `settings.json` it skips every non-policy hook source ([hooks-ref] "Disable all hooks").

### Non-interactive `-p` mode

| Fact | Source |
|---|---|
| Repo hooks in `.github/hooks/` load in `-p` only when the folder is already trusted | [changelog] 1.0.49 |
| `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS` (and `..._WORKSPACE_MCP`) gate repo hooks and workspace MCP in `-p` as opt-in env vars | [changelog] 1.0.40 |
| `COPILOT_ALLOW_ALL=true` (exactly `true`) trusts the working directory, loading its hooks; other truthy spellings only auto-approve tools | [help-env] |
| Folder trust persists across git worktrees without re-prompting | [changelog] 1.0.60 |
| `sessionEnd` fires once per completed agent turn (`reason` `complete` or `error`), not once at shutdown with `user_exit`; no `sessionEnd` if the run exits before a turn completes | [changelog] 1.0.78 |
| `-p` fires a single `sessionEnd` after `agentStop` continuations complete | [changelog] 1.0.92 |
| `sessionStart`/`sessionEnd` fire once per session in interactive mode | [changelog] 1.0.22 |
| Hook commands with no `cwd` run in the project root, not the session's current directory | [changelog] 1.0.88 |
| Lifecycle and subagent hook commands run in the current session directory after `/cd` | [changelog] 1.0.72 |

The last two rows conflict as written; 1.0.88 is the later release. Behavior when the project root differs from the worktree cwd is **UNVERIFIED**.

## Input payloads (stdin JSON)

camelCase event name gives camelCase fields and a numeric `timestamp` (epoch ms). PascalCase event name gives snake_case fields, `hook_event_name`, and an ISO 8601 `timestamp` ([hooks-ref] "Hook event input payloads"). All payloads carry `sessionId`/`session_id`, `timestamp`, `cwd`.

| Event | Extra fields (camelCase) |
|---|---|
| `sessionStart` | `source` (`startup`, `resume`, `new`), `initialPrompt?` |
| `sessionEnd` | `reason` (`complete`, `error`, `abort`, `timeout`, `user_exit`) |
| `userPromptSubmitted` | `prompt` |
| `userPromptTransformed` | `prompt`, `transformedPrompt` |
| `preToolUse` | `toolName`, `toolArgs` |
| `postToolUse` | `toolName`, `toolArgs`, `toolResult {resultType, textResultForLlm}` |
| `postToolUseFailure` | `toolName`, `toolArgs`, `error` |
| `agentStop` | `transcriptPath`, `stopReason`, `stop_hook_active` |
| `subagentStart` | `transcriptPath`, `agentName`, `agentDisplayName?`, `agentDescription?` |
| `subagentStop` | `transcriptPath`, `agentId`, `agentType`, `agentName`, `response`, `stopReason` |
| `preCompact` | `transcriptPath`, `trigger` (`manual`, `auto`), `customInstructions` |
| `errorOccurred` | `error {message, name, stack?}`, `errorContext`, `recoverable` |
| `notification` | `hook_event_name`, `message`, `title?`, `notification_type` |

Since 1.0.81 inputs also gain `traceparent` (and `tracestate`), and command hooks get matching env vars ([changelog]). Plugin hooks receive `PLUGIN_ROOT`, `COPILOT_PLUGIN_ROOT`, `CLAUDE_PLUGIN_ROOT` ([changelog] 1.0.26).

## Output and exit codes

Source: [hooks-ref] "Exit codes for command hooks" and the decision-control sections.

- stdout is parsed as one JSON document after progress lines (`{"type":"progress","message":"..."}`, single-line) are stripped. Empty or unparsable output means no decision. Two final JSON objects concatenate into invalid JSON; emit exactly one.
- Output is capped at 10 MiB per invocation.

| Exit | Effect |
|---|---|
| `0` | stdout parsed as hook output |
| `2` | Warning (stderr shown), run continues. `preToolUse`/`permissionRequest`: deny, even if stdout says allow. `postToolUseFailure`: stdout appended as `additionalContext`. |
| other non-zero | Logged, run continues (fail-open). `preToolUse`: deny (fail-closed). |
| timeout | Killed after `timeoutSec`, logged, continues. Fail-open for every event, including `preToolUse` and policy hooks. |

Decision shapes:

- `preToolUse`: `{permissionDecision: "allow"|"deny"|"ask", permissionDecisionReason, modifiedArgs}`. Reason is required on deny. Any deny among multiple hooks blocks the tool.
- `agentStop`/`subagentStop`: `{decision: "block"|"allow", reason, modifiedResponse}`; `reason` becomes the next-turn prompt. After 8 consecutive `block`s the CLI ends the turn; `stop_hook_active` lets a hook self-limit ([changelog] 1.0.72).
- `postToolUse`: `{modifiedResult: {resultType: "success", textResultForLlm}, additionalContext}`.
- `permissionRequest`: `{behavior: "allow"|"deny", message, interrupt}`; a hook `allow` never pre-approves a sandbox-bypass request.
- `sessionStart`, `subagentStart`, `notification`: `{additionalContext}`.

Hooks run synchronously and block the agent; keep them under about 5 seconds ([about-hooks] "Performance considerations").

## Usage

Log session start and end (repo-level `.github/hooks/log-session.json`):

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [
      { "type": "command", "bash": "echo \"start $(date -Is)\" >> logs/session.log", "timeoutSec": 10 }
    ],
    "sessionEnd": [
      { "type": "command", "bash": "./scripts/cleanup.sh", "timeoutSec": 60 }
    ]
  }
}
```

Deny `rm -rf` in `bash` tool calls (`.github/hooks/guard.json`, script must be executable):

```json
{
  "version": 1,
  "hooks": {
    "preToolUse": [
      { "type": "command", "matcher": "bash", "bash": "./scripts/guard.sh", "timeoutSec": 5 }
    ]
  }
}
```

```bash
#!/bin/bash
# guard.sh
INPUT=$(cat)
if echo "$INPUT" | jq -r '.toolArgs' | grep -q 'rm -rf'; then
  echo '{"permissionDecision":"deny","permissionDecisionReason":"rm -rf is blocked"}'
fi
```

Both snippets follow the schema in [hooks-ref]; the guard script is illustrative and was not executed.

Debugging ([hooks-howto] "Debugging", "Troubleshooting"):

- Pipe sample input into the script: `echo '{"timestamp":1704614400000,"cwd":"/tmp","toolName":"bash","toolArgs":"{\"command\":\"ls\"}"}' | ./my-hook.sh; echo $?`.
- Check JSON validity (`jq .`), `version: 1`, the file location, the executable bit and a shebang.
- Emit single-line JSON (`jq -c`).
- Write diagnostics to stderr.
- Increase `timeoutSec` for slow hooks.

Security ([about-hooks] "Security considerations", [hooks-ref]):

- Validate and sanitize hook input, and quote shell arguments.
- Do not log tokens or passwords.
- Command `preToolUse` hooks fail closed on errors but open on timeout; HTTP `preToolUse` hooks fail open on network errors, timeouts and non-2xx.
- With the session sandbox on, repo, user and plugin command hooks run inside it; policy hooks run on the host.
- `COPILOT_ALLOW_ALL=true` trusts the cwd and so loads any hooks the agent wrote into it ([help-env]).

## Check against the prototype plan

Compared with [agent-cli-hooks-prototype.md](../plans/agent-cli-hooks-prototype.md):

| Plan assumption | Verdict |
|---|---|
| Event names `sessionStart`, `sessionEnd`, `agentStop`, `preToolUse`, `postToolUse`, `subagentStart`, `subagentStop`, `preCompact` | Confirmed |
| Config `{"version":1,"hooks":{<camelEvent>:[{"type":"command","bash":...,"timeoutSec":n}]}}` | Confirmed |
| Payload fields `sessionId`, `source`, `reason`, `toolName`; `resumed = source == "resume"` | Confirmed (`source` is `startup`, `resume` or `new`) |
| File `.github/hooks/loop-<hex>.json` is loaded in `-p` mode from the worktree | **Contradicted as stated:** repo hooks load in `-p` only if the folder is trusted or `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=true`. The runner must set the env var or ensure trust, and the worktree trust is inherited per 1.0.60. |
| `sessionEnd` fires in `-p` | Partly confirmed: once per completed turn with `complete`/`error`, none if the run exits early. A once-per-run `SESSION_END` semantic needs a note. |
| `PROMPT_SUBMITTED` is unsupported because "Copilot doesn't fire prompt hooks in `-p`" | Confirmed only for `type: "prompt"` hooks. Whether the `userPromptSubmitted` event fires in `-p` is **UNVERIFIED**; the docs show it firing once under cloud agent. |
| Hook `cwd` is the agent cwd | **UNVERIFIED** (1.0.72 and 1.0.88 disagree; set `cwd` explicitly). |
| Shim always exits 0 and discards stdout, so hooks only observe | Consistent with the docs: exit 0 with empty stdout means no decision. |
| `git_excludes` for `.github/hooks/loop-*.json` | Not covered by the sources. |
| Rejecting `COPILOT_HOME` because it "also moves auth" | Docs confirm `COPILOT_HOME` moves the user hooks directory; the auth claim is **UNVERIFIED**. |

Additional options the sources show:

- `exec` + `args` (CLI only) removes shell quoting, so `shlex.join` is unnecessary.
- `env` on the entry can carry `LOOP_SESSION` and similar variables.
- `COPILOT_AGENT_SESSION_ID` is exported to shell commands and MCP servers ([changelog] 1.0.29); whether hook commands receive it is **UNVERIFIED**.

## Sources

- [hooks-ref]: GitHub Copilot hooks reference — https://docs.github.com/en/copilot/reference/hooks-configuration (also at `/en/copilot/reference/hooks-reference`)
- [hooks-howto]: Using hooks with GitHub Copilot CLI — https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-hooks
- [about-hooks]: About hooks for GitHub Copilot — https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-hooks
- [changelog]: github/copilot-cli changelog — https://raw.githubusercontent.com/github/copilot-cli/main/changelog.md (entries 1.0.22, 1.0.26, 1.0.29, 1.0.40, 1.0.49, 1.0.51, 1.0.60, 1.0.72, 1.0.78, 1.0.81, 1.0.88, 1.0.92)
- [help-env]: `copilot help environment`, CLI 1.0.95, run locally 2026-10-10 (`COPILOT_ALLOW_ALL`)

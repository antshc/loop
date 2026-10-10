# Codex CLI hooks

Hooks supported by OpenAI Codex CLI, as verified 2026-10-10 against the local `codex-cli 0.162.1`, the official [Hooks docs](https://developers.openai.com/codex/hooks) (redirects to `learn.chatgpt.com/docs/hooks`, fetched 2026-10-10), and `openai/codex` `main` @ `806d973` (2026-10-10). `main` is ahead of 0.162.1 (tags up to `rust-v0.163.0-alpha.6` exist), so source-derived facts may differ from the installed release; those are marked "source". Nothing here was executed: no agent session was started and no config was changed.

## Usage

Hooks are on by default (`codex features list` shows `hooks  stable  true`). Put a `hooks.json` next to a config layer, trust it, run.

`~/.codex/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume",
        "hooks": [
          { "type": "command", "command": "python3 /abs/path/hook.py", "timeout": 30 }
        ]
      }
    ]
  }
}
```

`/abs/path/hook.py` (receives one JSON object on stdin; stdout text becomes developer context for `SessionStart`):

```python
import json, sys
event = json.load(sys.stdin)
print(f"session {event['session_id']} started via {event['source']} in {event['cwd']}")
```

Interactive: start `codex`, open `/hooks`, review and trust the hook. Non-interactive automation that already vets its hook sources:

```bash
codex exec --dangerously-bypass-hook-trust "prompt"
```

Source: [Hooks docs](https://developers.openai.com/codex/hooks); `codex exec --help` (0.162.1).

## Events

12 events (source: [`HookEventsToml`](https://github.com/openai/codex/blob/806d973/codex-rs/config/src/hook_config.rs); docs list the same set). `matcher` is a regex; `"*"`, `""` or omitted matches all.

| Event | Fires | `matcher` filters | Fires in `codex exec` |
|---|---|---|---|
| `SessionStart` | Session starts (`startup`), resumes, is cleared, or after compaction | `source`: `startup`, `resume`, `clear`, `compact` | Yes, confirmed by an exec test with `hooks.json` ([`exec/tests/suite/hooks.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/exec/tests/suite/hooks.rs)) |
| `SessionEnd` | Main thread ends: archive/delete of an open conversation, normal close, or 30 min idle with no connected client. Not for subagents. `thread/unsubscribe` alone does not fire it | `reason`; always `other` today | Unverified; see [7c](#open-questions) |
| `UserPromptSubmit` | Before a prompt is sent to the model | not supported (ignored) | Unverified (shared turn loop, untested in exec) |
| `PreToolUse` | Before a tool call: shell (`Bash`), unified exec (`Bash`), `apply_patch` (`apply_patch`/`Edit`/`Write`), MCP tools (`mcp__server__tool`), other local function tools. Not hosted tools such as `WebSearch` | tool name | Unverified (shared turn loop, untested in exec) |
| `PermissionRequest` | Codex is about to ask for approval (shell escalation, managed-network approval). Not for commands that need no approval | tool name | Unverified |
| `PostToolUse` | After a supported tool produces output, including non-zero Bash exits | tool name | Unverified (shared turn loop, untested in exec) |
| `PreCompact` | Before chat compaction | `trigger`: `manual`, `auto` | Unverified |
| `PostCompact` | After chat compaction | `trigger`: `manual`, `auto` | Unverified |
| `SubagentStart` | A subagent starts | `agent_type` | Unverified |
| `SubagentStop` | A subagent finishes | `agent_type` | Unverified |
| `Stop` | Turn is about to end | not supported (ignored) | Unverified (shared turn loop, untested in exec) |
| `Interrupt` | User interrupts an active main-thread turn. Not for idle threads or subagents | not supported (ignored) | Unverified |

Source: [Hooks docs](https://developers.openai.com/codex/hooks) (When / Matcher patterns / Tool coverage sections).

## Where hooks are configured

Codex discovers hooks next to each active config layer, in either form ([docs](https://developers.openai.com/codex/hooks)):

| Location | Scope |
|---|---|
| `~/.codex/hooks.json`, `~/.codex/config.toml` `[hooks]` | User |
| `<repo>/.codex/hooks.json`, `<repo>/.codex/config.toml` `[hooks]` | Project; loaded only when the project `.codex/` layer is trusted |
| System / MDM / cloud / `requirements.toml` `[hooks]` | Managed (trusted by policy, cannot be disabled by the user) |
| `-c hooks.<Event>=[...]` on the command line | Session flags layer (source: `ConfigLayerSource::SessionFlags`, [`discovery.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/hooks/src/engine/discovery.rs)) |
| Enabled plugins: `hooks/hooks.json` or manifest `hooks` entry | Plugin; same trust review as other non-managed hooks |

Precedence: there is none for replacement. All matching hooks from all sources run; higher layers do not override lower ones. Matching command hooks for one event start concurrently. If one layer has both `hooks.json` and `[hooks]`, Codex merges both and warns at startup ([docs](https://developers.openai.com/codex/hooks)). Source: discovery order is managed requirements, then config layers low to high, then plugins ([`discover_handlers`](https://github.com/openai/codex/blob/806d973/codex-rs/hooks/src/engine/discovery.rs)).

Enable/disable: `[features] hooks = false` turns hooks off (`codex_hooks` is a deprecated alias). Admins can pin it in `requirements.toml`; `allow_managed_hooks_only = true` there ignores user, project, session and plugin hooks ([docs](https://developers.openai.com/codex/hooks), [docs/config.md](https://github.com/openai/codex/blob/806d973/docs/config.md)).

### Schema

Three levels: event, matcher group, handlers. Source: [`hook_config.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/config/src/hook_config.rs).

| Field | Where | Meaning |
|---|---|---|
| `description` | `hooks.json` top level | Optional metadata; the file rejects other unknown top-level keys |
| `hooks.<Event>[]` | root | List of matcher groups |
| `matcher` | group | Regex; see Events table |
| `hooks[]` | group | Handlers |
| `type` | handler | `command` or `mcp_tool`; `prompt` and `agent` parse but are skipped |
| `command` | `command` | Shell command line |
| `commandWindows` / `command_windows` | `command` | Windows-only override |
| `timeout` | handler | Seconds; default 600, minimum 1. `SessionEnd` and `Interrupt`: default 1, clamped to 1-3 |
| `async` | `command` | Run in the background; `SessionEnd` always runs synchronously |
| `statusMessage` | handler | Text shown while the hook runs |
| `additionalContextLimit` | `command` | Token threshold before `additionalContext` spills to disk; default 2500, `0` disables |
| `server`, `tool`, `input` | `mcp_tool` | Connected MCP server, tool, argument templates using `${field.nested}`; `SessionEnd` does not support it |

Equivalent inline TOML ([docs](https://developers.openai.com/codex/hooks)):

```toml
[[hooks.SessionStart]]
matcher = "startup|resume"

[[hooks.SessionStart.hooks]]
type = "command"
command = "python3 /abs/path/hook.py"
timeout = 30
```

Command-line override: `-c hooks.SessionStart=[{matcher="startup|resume",hooks=[{type="command",command="python3 /abs/path/hook.py"}]}]`. `-c key=value` takes a dotted path and parses the value as TOML (`codex exec --help`, 0.162.1). See [7a](#open-questions).

## Hook input and output

Every command hook gets one JSON object on stdin ([docs](https://developers.openai.com/codex/hooks)).

| Field | Events | Meaning |
|---|---|---|
| `session_id` | all | Session id (parent id for subagent hooks) |
| `transcript_path` | all | Transcript file or `null`; format not stable |
| `cwd` | all | Session working directory |
| `hook_event_name` | all | Event name |
| `model` | all | Active model slug |
| `permission_mode` | all but `SessionEnd`, `PreCompact`, `PostCompact` | `default`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions` |
| `turn_id` | turn-scoped events | Active turn id |
| `source` | `SessionStart` | `startup`, `resume`, `clear`, `compact` |
| `reason` | `SessionEnd` | `other` |
| `trigger` | `PreCompact`, `PostCompact` | `manual`, `auto` |
| `prompt` | `UserPromptSubmit` | Prompt about to be sent |
| `tool_name`, `tool_use_id`, `tool_input` | `PreToolUse`, `PostToolUse` (`PermissionRequest`: no `tool_use_id`) | Canonical tool name, call id, input (`tool_input.command` for Bash and `apply_patch`) |
| `tool_response` | `PostToolUse` | Tool output |
| `agent_id`, `agent_type` | `SubagentStart`, `SubagentStop` | Subagent identity |
| `agent_transcript_path`, `stop_hook_active`, `last_assistant_message` | `SubagentStop` | Subagent transcript, already continued, last message |
| `stop_hook_active`, `last_assistant_message` | `Stop` | Already continued by `Stop`, last assistant message |

### Output semantics

| Result | Effect |
|---|---|
| Exit 0, no output | Success, continue |
| Exit 0, plain text | Extra developer context for `SessionStart`, `UserPromptSubmit`, `SubagentStart`; ignored for `PreToolUse`, `PermissionRequest`, `PostToolUse`, compaction; invalid for `Stop`, `SubagentStop`, `Interrupt` |
| Exit 0, JSON `{"continue": false, "stopReason": "..."}` | Marks that hook run stopped (`PostToolUse`: replaces the tool result; compaction hooks: stop before/after compacting; not supported on `PreToolUse`/`PermissionRequest`; does not stop `SubagentStart`) |
| JSON `systemMessage` | Shown as a warning |
| JSON `hookSpecificOutput.additionalContext` | Added as developer context (`SessionStart`, `UserPromptSubmit`, `SubagentStart`, `PreToolUse`, `PostToolUse`) |
| `PreToolUse` deny | `hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason}`, or `{"decision":"block","reason"}`, or exit 2 with the reason on stderr |
| `PreToolUse` allow with rewrite | `permissionDecision: "allow"` plus `updatedInput` (Bash and `apply_patch`: object with a string `command`) |
| `PermissionRequest` | `hookSpecificOutput.decision.behavior` `allow` or `deny` (+ `message`); any `deny` wins; no decision falls back to the normal prompt |
| `PostToolUse` block | `{"decision":"block","reason"}` or exit 2: tool result replaced by the feedback; side effects not undone |
| `UserPromptSubmit` block | `{"decision":"block","reason"}` or exit 2 with stderr |
| `Stop` / `SubagentStop` continue | `{"decision":"block","reason"}` or exit 2 with stderr: Codex continues with `reason` as a new prompt. `continue: false` from any matching hook wins |
| Exit 2 with empty stderr | Does not block (source: [`stop.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/hooks/src/events/stop.rs) test `exit_code_two_without_stderr_does_not_block`) |
| Other non-zero exit, timeout, spawn error | Reported as a hook failure; the operation continues (source: `hook exited with code N` branch in [`pre_tool_use.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/hooks/src/events/pre_tool_use.rs)) |
| Unsupported fields (`permissionDecision: "ask"`, `suppressOutput`, `updatedMCPToolOutput`, ...) | Hook run marked failed; the operation continues |

Each model-visible hook output is capped near 2,500 tokens; the rest spills to `<temp_dir>/hook_outputs/<session_id>/<uuid>.txt` ([docs](https://developers.openai.com/codex/hooks)). `PreToolUse`/`PostToolUse` are guardrails, not a complete enforcement boundary ([docs](https://developers.openai.com/codex/hooks)).

## Trust and approval

- Non-managed hooks (user, project, session, plugin) must be reviewed and trusted before they run. New or changed hooks are skipped and Codex prints a startup warning pointing to `/hooks` ([docs](https://developers.openai.com/codex/hooks)).
- `/hooks` in the interactive CLI inspects sources, trusts hooks, and disables individual non-managed hooks ([docs](https://developers.openai.com/codex/hooks)). It persists trust by writing `hooks.state` through a config edit with `trusted_hash` ([`tui/src/hooks_rpc.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/tui/src/hooks_rpc.rs), source).
- Persisted form (source, [`hook_config.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/config/src/hook_config.rs), [`config_rules.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/hooks/src/config_rules.rs)): `[hooks.state."<key>"]` with `enabled` and `trusted_hash`. State is read only from the User and SessionFlags layers; later layers win per field.
- Key (source, `hook_key`): `<absolute source path>:<event snake_case>:<group_index>:<handler_index>`, for example `.../hooks.json:session_start:0:0`. Source path is the `hooks.json` or `config.toml` file; session-flag hooks use the synthetic path `<session-flags>/config.toml`.
- Hash (source, `hook_hash`): `sha256:` over a normalized definition (event, matcher, handler fields). It is not a hash of the file text, so equivalent hooks in `hooks.json` and `config.toml` share it. The process environment is not an input.
- Status (source, `hook_trust_status`): `Trusted` (hash matches), `Modified` (stored hash differs), `Untrusted` (none), `Managed` (policy). Only enabled hooks that are `Trusted` or `Managed` run.
- `--dangerously-bypass-hook-trust` ([`codex`, `codex exec`, `resume`](https://github.com/openai/codex/blob/806d973/codex-rs/cli/src/main.rs)): "Run enabled hooks without requiring persisted hook trust for this invocation". Disabled hooks (`enabled = false`) stay disabled (source: test `bypass_hook_trust_respects_disabled_handlers`). Codex logs a warning that enabled hooks may run without review ([`config/mod.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/core/src/config/mod.rs)). It also covers hooks an agent writes into the worktree's trusted project layer (risk noted in the plan, see [agent-cli-hooks-prototype.md](../plans/agent-cli-hooks-prototype.md)).
- Project hooks additionally need the project `.codex/` layer to be trusted; bypass is about hook trust, and the docs do not say it overrides project-layer trust (unverified).
- Managed hooks need no review. With `allow_managed_hooks_only = true`, only managed hooks load.

## Runtime environment

All source ([`command_runner.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/hooks/src/engine/command_runner.rs), [`registry.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/hooks/src/registry.rs), [`shell_environment.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/protocol/src/shell_environment.rs)) unless marked docs.

| Aspect | Behavior |
|---|---|
| Working directory | Session `cwd` ([docs](https://developers.openai.com/codex/hooks)). Codex may start in a subdirectory; resolve repo scripts from `$(git rev-parse --show-toplevel)` |
| Shell | The turn environment's shell if set, else `$SHELL` from the environment, else `/bin/sh`; run as `<shell> -lc "<command>"` on Unix |
| Stdin | The event JSON, written then closed |
| Environment | Snapshot of the Codex process env taken when hooks are created (`std::env::vars_os()`), minus `CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN`, `NODE_REPL_AUTH_TOKEN`, `CODEX_GUARDIAN_DECISIONS_API_KEY`, `OPENAI_FEDERATION_RULE_ID`, `OPENAI_IDENTITY_TOKEN_FILE`, `OPENAI_WORKLOAD_IDENTITY_CONTEXT`. No per-hook `env` field in the schema |
| Plugin hooks | Also get `PLUGIN_ROOT`, `PLUGIN_DATA`, `CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA` ([docs](https://developers.openai.com/codex/hooks)) |
| `CODEX_THREAD_ID` / `CODEX_SESSION_ID` in hook env | Unverified; defined as constants but not set by the hook runner |
| Timeout | `tokio::time::timeout` over stdin write + wait; on expiry the hook is reported as `hook timed out after Ns` and its process group is killed |
| Process | Own session/process group on Unix; killed on drop unless it completed |
| Sandbox | The runner spawns the command directly from the Codex process with no sandbox wrapper; the docs do not describe hook sandboxing, so treat hooks as running with the user's full privileges |
| Background | `async: true` runs up to 8 per session concurrently; output delivered at the next safe point; cancelled at session end ([docs](https://developers.openai.com/codex/hooks)) |

## Examples

Not executed; derived from the docs and schema above.

Block destructive Bash via `PreToolUse` (`~/.codex/hooks.json`):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "python3 /abs/path/deny_rm.py", "timeout": 10 }]
      }
    ]
  }
}
```

```python
import json, sys
event = json.load(sys.stdin)
if "rm -rf" in event["tool_input"].get("command", ""):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "rm -rf is blocked by hook."}}))
```

Observe session end and turn end, one-off from the command line with trust bypass:

```bash
codex exec --dangerously-bypass-hook-trust \
  -c 'hooks.SessionEnd=[{hooks=[{type="command",command="python3 /abs/path/log.py",timeout=3}]}]' \
  -c 'hooks.Stop=[{hooks=[{type="command",command="python3 /abs/path/log.py"}]}]' \
  "prompt"
```

`log.py` must print JSON (for example `{"continue": true}`) for `Stop`, because plain text is invalid there:

```python
import json, sys
event = json.load(sys.stdin)
open("/tmp/codex-hooks.log", "a").write(json.dumps(event) + "\n")
if event["hook_event_name"] == "Stop":
    print(json.dumps({"continue": True}))
```

## Open questions

| # | Question | Answer |
|---|---|---|
| 7a | Does Codex accept `-c hooks.SessionStart=[...]`? | Confirmed by source, not by execution. `-c` overrides become the SessionFlags config layer, and discovery loads `hooks` from every layer including SessionFlags ([`discovery.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/hooks/src/engine/discovery.rs) `hook_metadata_for_config_layer_source`, `load_toml_hooks_from_layer`). Key names are case-sensitive (`SessionStart`). Such hooks are non-managed, so they are untrusted without the bypass flag or a stored `trusted_hash` |
| 7b | Does persisted trust survive per-run env changes (changed env var, `CODEX_HOME`)? | Env vars: yes, by source; the hash covers the hook definition only and the key covers path, event and indexes. `CODEX_HOME`: no, by source inference; trust lives in `hooks.state` in the user `config.toml` of the active home, and keys embed the source file path, so a new home shows hooks as `Untrusted`. Likewise a moved or new worktree changes project-hook keys. Editing the command, matcher, timeout or index position makes it `Modified`/`Untrusted`. Not executed. `<session-flags>/config.toml` keys are path-independent (inference: a stored `trusted_hash` would match across runs) |
| 7c | Does `codex exec` fire `SessionStart`, `SessionEnd`, `Stop`? | `SessionStart`: confirmed (exec integration test, with the bypass flag and `$CODEX_HOME/hooks.json`). `Stop`: unverified; core turn loop is shared ([`hook_runtime.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/core/src/hook_runtime.rs) `run_stop`) but no exec test. `SessionEnd`: unverified; `exec` calls `thread/unsubscribe` then shuts down the in-process app-server, whose `thread_manager` shutdown calls `shutdown_and_wait` per thread (10 s timeout in app-server paths), and the session shutdown handler runs `run_session_end_hooks` ([`handlers.rs`](https://github.com/openai/codex/blob/806d973/codex-rs/core/src/session/handlers.rs)). The docs say `thread/unsubscribe` alone does not fire it, so a `SessionEnd` hook with the 1-3 s limit may be cut short; verify empirically |

## Unverified

- Whether bypass overrides project-layer trust for untrusted projects.
- Hook firing in `codex exec` for every event except `SessionStart`.
- Hook env containing `CODEX_THREAD_ID` or `CODEX_SESSION_ID`.
- Hook sandboxing semantics beyond the observed direct spawn.
- Whether source facts (`main` @ `806d973`) match release 0.162.1 exactly.
- Release or changelog entry for when hooks became stable and default-on (`CHANGELOG.md` in the repo is a 5-line pointer).
- `UserPromptSubmit` plain-text/JSON edge cases and `Stop` behavior on invalid JSON beyond the docs ("invalid").

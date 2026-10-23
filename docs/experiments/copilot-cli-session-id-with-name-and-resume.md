# Experiment: Copilot `--session-id` with `--name`, and `--resume` by id or name

Answers the open question behind [copilot-cli-telemetry-extraction.md](copilot-cli-telemetry-extraction.md): how can an optional Loop observer find a turn's `events.jsonl`, given that `CopilotCli` starts a session with `--name <session name>` and resumes it with `--resume=<name>`? Run 2026-10-23.

## Question

1. Does `copilot -p` accept `--name X` together with `--session-id <uuid>` on a new session, and does the log then live at `session-state/<uuid>/events.jsonl`?
2. Does `--resume=<uuid>` continue that session, and does `--resume=<name>` (what `CopilotCli` does today) reach the same session?
3. With `--name` only (today's `CopilotCli`), can the log's directory be recovered?
4. What happens when `--name` and `--session-id` name an id that already exists?
5. Are usage records per turn, so a resumed turn can be sliced?

Out of scope: Docker, concurrent runs on one session, `--continue`.

## Method

- **CLI under test:** GitHub Copilot CLI 1.0.103, `~/.copilot` as `COPILOT_HOME`, model `claude-haiku-4.5`.
- **Scratch:** `/tmp/loop-cop-sid-exp/main`, a git repo with one empty commit. Every run started from it.
- **Common flags:** `--output-format json --no-color --no-ask-user --autopilot --allow-all --model claude-haiku-4.5`, prompt `Reply with the single word <WORD>`, stdout and stderr captured.
- **Cases:**
  - A: `--name exp-a --session-id <U>`, a uuid chosen by the caller.
  - B: `--resume=<U>`.
  - C: `--resume=exp-a`.
  - D: `--name exp-d` only, then D2: `--resume=exp-d`.
  - E: `--name exp-e --session-id <U>` with `<U>` from A (already existing).
- B and C were started at the same time by mistake, so both resumed `<U>` concurrently. Their file layout is valid; their cost and AIU totals are not (see Observations).

## Observations

- **A:** exit 0. The log is `session-state/<U>/events.jsonl`; the dir also holds `workspace.yaml`. `workspace.yaml` has `id: <U>`, `name: exp-a`, `user_named: true`, `cwd: /tmp/loop-cop-sid-exp/main`, `git_root`, `branch`. `session.start` carries `sessionId`, `copilotVersion`, `selectedModel`, and `context.cwd`. The JSON stdout stream has `sessionId: <U>` on every event.
- **B:** exit 0, same `sessionId`. A `session.resume` event (with `context.cwd`, `eventCount`, `resumeTime`) was appended to the same `events.jsonl`, followed by that turn's `user.message`, `session.usage_record`, `assistant.message` and `session.shutdown`.
- **C:** exit 0, same `sessionId` `<U>`. `--resume=exp-a` resolved the name to the session started in A, and appended to the same file.
- **D:** exit 0. The CLI assigned its own id: stdout reported `sessionId: e6099d93-3f39-443c-9f1f-52b1fcbe050f`, and the log is `session-state/<that id>/`. Nothing in the process or its argv carried that id before it ran.
- **D2:** `--resume=exp-d` exit 0, same `sessionId`. This is how `CopilotCli` resumes today; name resolution works.
- **E:** exit 1 before any model call, stderr: `error: option '-n, --name <name>' cannot be used with option '--session-id <id>' when it resolves to an existing or remote session or task. --name only names a brand-new session created by --session-id=<new-uuid>; the resolved session was not renamed.` No `events.jsonl` was touched.
- **Usage records:** each turn wrote one `session.usage_record` (`usage.inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`, `reasoningTokens`, `cost`) between that turn's `user.message` and `session.shutdown`. `session.shutdown.accountingSnapshot` is cumulative over the session (`totalPremiumRequestCost` 0.33 after A, 0.66 after B; `totalUserRequests` 1 then 2). The AIU total after C differs from B's, which is the concurrency artifact: C resumed from the state before B's turn.
- **Cost:** five model calls at 0.33 premium requests each, 1.65 in total.

## Conclusion

1. `--name` and `--session-id` combine on a new session. A caller that mints the uuid knows the log path (`<COPILOT_HOME>/session-state/<uuid>/events.jsonl`) before the process starts, and the human name still shows up in `workspace.yaml`.
2. Resume works by either the uuid or the name, and appends to the same file. So `CopilotCli` can keep resuming by name; only the Start needs the extra flag. The recovered id is also the one to resume by, so `NativeHandle.value` may hold either.
3. With `--name` only, the id is assigned by the CLI and appears only in the output: the `sessionId` of the JSON stream. With the default `CopilotText` transport it is not in stdout. An observer that wants it without pinning would need `CopilotJson` (or equivalent), or a directory scan by `cwd` and time window (`session.start`/`workspace.yaml` both record the cwd; a scan is unverified here).
4. Reusing an existing id on a Start fails fast with exit 1 and a distinct message. An observer needs no special handling; the run raises like any non-zero CLI exit.
5. Per-turn data comes from `session.usage_record`. A resumed turn is sliced from its `session.resume` (or `session.start`) event to its `session.shutdown`. `session.shutdown` totals are cumulative and must not be summed across turns.

Consequence for the optional observability extension: pin the id on Start (`--name <name> --session-id <uuid5 of the name>` or a fresh uuid saved in the handle) and read the log by id; keep the name for resume. Resuming works unchanged.

## Not verified

- Two processes on one session at once. B and C collided in this run; the CLI did not error, but the totals were inconsistent.
- Resuming a session that was started with `--session-id` but whose handle was saved as the name, after the name is reused in another worktree.
- Whether `--session-id` plus `--resume=<name>` is accepted when the name resolves ambiguously (two sessions with one name).
- A directory scan keyed by `cwd` and time window, as a fallback for sessions started without a pinned id.
- Codex CLI. Its session log and resume behaviour were not examined.

## Evidence

- Scratch outputs `/tmp/loop-cop-sid-exp/{a,b,c,d,d2,e}.{out,err}` (not kept).
- Logs under `~/.copilot/session-state/6f1d2c3a-0000-4000-8000-00000000000a/` and `.../e6099d93-3f39-443c-9f1f-52b1fcbe050f/`.

## Cleanup

The scratch repo and the two session directories created by this experiment were removed after the run. No repository file other than this note was changed.

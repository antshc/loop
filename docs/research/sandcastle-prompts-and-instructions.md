# Sandcastle: how prompts and instructions reach the agent

Source: https://github.com/mattpocock/sandcastle @ `e99f832f26dc9d245c019a9ddd19fa5dee792427` (shallow clone, read-only)

## Summary

- Sandcastle ships **no built-in task instructions**. The library calls `run()` and `createSandbox()`, and the caller must supply exactly one of `prompt` (an inline string) or `promptFile` (a path). Otherwise `run()` fails (`src/PromptResolver.ts:L28-L47`). The README states "no opinions about workflow… are imposed" (`README.md:L562`).
- **Inline prompts are passed through as-is.** **Prompt files** are templates: `{{KEY}}` substitution runs on the host, then `` !`cmd` `` shell expansion runs inside the sandbox. The prompt's source decides which path applies, not an option flag (`src/PromptResolver.ts:L10-L21`, `src/run.ts:L713-L734`, `docs/adr/0008-inline-prompts-skip-processing.md`).
- **Default prompt files** exist only as scaffolds. `sandcastle init` copies a template's `*.md` prompts and `main.mts` into `.sandcastle/` and bakes issue-tracker commands into them. At runtime, Sandcastle reads `.sandcastle/prompt.md` only when `promptFile` names it (`README.md:L577`, `src/InitService.ts:L1073`, `src/InitService.ts:L875-L910`).
- **Variables:** user `promptArgs` plus two built-in arguments, `SOURCE_BRANCH` and `TARGET_BRANCH`, that callers cannot override. A placeholder with no value is a hard error; an unused argument only warns (`src/PromptArgumentSubstitution.ts:L19-L22`, `src/PromptArgumentSubstitution.ts:L121-L147`).
- **Hardcoded per agent:** only the agent CLI's command line (flags, output format, permission bypass) and the channel that carries the prompt. Claude, Codex, and Pi receive it on **stdin**; Cursor, OpenCode, and Copilot receive it as an **argv** argument (`src/AgentProvider.ts:L1213-L1214`, `src/AgentProvider.ts:L1131`).
- **The only prompt text Sandcastle writes itself** is the structured-output retry feedback (`src/run.ts:L59-L88`). The completion signal `<promise>COMPLETE</promise>` is **detected, never injected** (`src/Orchestrator.ts:L246`, `README.md:L645`).
- **Agent-native instruction channels are untouched.** `src/` has no handling of `AGENTS.md`, `CLAUDE.md`, skills, system prompts, or custom agents. The only exception is OpenCode's optional `--agent` passthrough (`src/AgentProvider.ts:L973-L975`). Those files reach the agent only through its CLI's own discovery in the worktree.
- **There is no prompt CLI.** The `sandcastle` binary has only `init`, `docker`, and `podman` commands (`src/cli.ts:L684`). Prompts are configured in a user-owned TypeScript entry file (`.sandcastle/main.mts`).

## Instruction sources

| Instruction source | Kind (hardcoded/argument/config/file) | Where read (path:line) | Injection mechanism |
| --- | --- | --- | --- |
| Inline prompt (`RunOptions.prompt`) | argument (JS API option) | `src/PromptResolver.ts:L36-L38`; `src/run.ts:L597-L603` | Passed verbatim through `orchestrate` into `provider.buildPrintCommand` (stdin or argv depending on the provider) |
| Prompt template (`RunOptions.promptFile`) | file | `src/PromptResolver.ts:L49-L61` (`fs.readFileString`) | Substitution, then expansion, then `buildPrintCommand` (stdin or argv) |
| `promptArgs` `{{KEY}}` values | argument (JS API option) | `src/run.ts:L713-L734`; `src/PromptArgumentSubstitution.ts:L87-L157` | Placeholder string replacement on the host before expansion |
| Built-in args `{{SOURCE_BRANCH}}` / `{{TARGET_BRANCH}}` | hardcoded (keys), computed (values) | `src/PromptArgumentSubstitution.ts:L19-L22`; `src/run.ts:L723-L727` | Merged ahead of user args, then substituted |
| `` !`command` `` shell expressions | file (inside the template) | `src/PromptPreprocessor.ts:L18-L21`, `src/PromptPreprocessor.ts:L43-L54` | Executed inside the sandbox at the repo dir; stdout is spliced into the prompt each iteration (`src/Orchestrator.ts:L406-L414`) |
| Scaffolded prompts (`prompt.md`, `plan-`/`implement-`/`review-`/`merge-prompt.md`, `CODING_STANDARDS.md`) | file (default, user-editable) | Copied at init: `src/InitService.ts:L729-L755`, `src/InitService.ts:L1073` | Become `promptFile` targets referenced in `main.mts` (for example `src/templates/simple-loop/main.mts:L22`) |
| Issue-tracker commands `{{LIST_TASKS_COMMAND}}`, `{{VIEW_TASK_COMMAND}}`, `{{CLOSE_TASK_COMMAND}}` | hardcoded registry, init-time | `src/InitService.ts:L530-L571`; substituted by `src/InitService.ts:L875-L910` | Written into the scaffolded `.md` files once, at init |
| Agent, model, and flags | argument (provider factory in `main.mts`) | `src/AgentProvider.ts:L1109-L1133` (copilot), `src/AgentProvider.ts:L1181-L1216` (claude) | Command string built by `buildPrintCommand`; flags are hardcoded per provider |
| Structured-output instruction | file or argument (the caller writes it) | Validated at `src/run.ts:L606-L614` | Not injected; `run()` fails if the opening `<tag>` is missing from the prompt |
| Structured-output retry feedback | hardcoded | `src/run.ts:L59-L88`; used at `src/run.ts:L866-L888` | Sent as a new inline prompt on a resumed session |
| `resume(prompt)` / `fork(prompt)` follow-ups | argument (JS API) | `src/run.ts:L805-L838` | Inline prompt plus `resumeSession`, single iteration |
| Completion signal `<promise>COMPLETE</promise>` | hardcoded default, overridable via `completionSignal` | `src/Orchestrator.ts:L246`, `src/Orchestrator.ts:L332-L339` | Detection only; the prompt author must instruct the agent to emit it |
| Env vars (`.sandcastle/.env` and `process.env`, plus provider `env`) | config | `src/EnvResolver.ts:L56-L66` | Sandbox and process environment (not prompt text) |
| Interactive session prompt | argument or file | `src/createWorktree.ts:L301-L336`, `src/createWorktree.ts:L423-L436` | `buildInteractiveArgs`, an argv seed (copilot uses `-i <prompt>`, `src/AgentProvider.ts:L1142`) |
| `SETUP_ISSUE_TRACKER.md` (custom tracker) | hardcoded doc, written at init | `src/InitService.ts:L1095-L1105` | The user feeds it to a host agent manually, for example `copilot -i "$(cat …)"` (`src/InitService.ts:L475`) |
| `AGENTS.md`, `CLAUDE.md`, skills, custom agents | not handled by Sandcastle | No references in `src/` | Left to the agent CLI's native discovery in the worktree. Templates rely on Claude's `@file` include (`src/templates/parallel-planner-with-review/review-prompt.md:L41`) |

## Detail

### 1. Prompt resolution (inline vs. template)

- `resolvePrompt` sets up the inline-versus-template split:
  - It rejects a call that sets both `prompt` and `promptFile` (`src/PromptResolver.ts:L28-L34`) and a call that sets neither (`src/PromptResolver.ts:L40-L47`).
  - An inline prompt returns `{ source: "inline" }` (`src/PromptResolver.ts:L36-L38`).
  - A file prompt is read with `fs.readFileString` and returns `{ source: "template" }` (`src/PromptResolver.ts:L49-L61`).
- `promptFile` resolves against `process.cwd()`, not the `cwd` option (`src/run.ts:L349-L356`, `README.md:L177`).
- ADR 0008 explains the rule. Inline prompts often embed arbitrary content, such as issue bodies, that may contain `{{…}}`, so only file prompts are processed (`docs/adr/0008-inline-prompts-skip-processing.md`).
- Passing `promptArgs` with an inline prompt is an error (`src/PromptArgumentSubstitution.ts:L35-L45`, `src/run.ts:L718-L720`).

### 2. Templating: `{{KEY}}` substitution (host side)

- Placeholder grammar is `\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}` (`src/PromptArgumentSubstitution.ts:L24`).
- The effective arguments are `{ SOURCE_BRANCH, TARGET_BRANCH, ...userArgs }` (`src/run.ts:L723-L727`). The built-in keys cannot be overridden (`src/PromptArgumentSubstitution.ts:L47-L60`).
  - In `run()`, `TARGET_BRANCH` is the host's current branch.
  - In interactive worktree sessions, both built-in keys are set to the worktree branch (`src/createWorktree.ts:L324-L325`).
- A missing or null value fails the run. An unused argument logs a warning (`src/PromptArgumentSubstitution.ts:L121-L147`).
- Injection safety: before substitution, `` !`…` `` blocks in the raw template are tagged with a `\x01` marker, and any markers already present in the argument values are stripped. As a result, `` !`…` `` text that arrives through `promptArgs` is never executed (`src/PromptArgumentSubstitution.ts:L95-L105`, `src/PromptPreprocessor.ts:L9-L21`, `README.md:L624`).

### 3. Templating: `` !`command` `` expansion (sandbox side)

- `preprocessPrompt` runs every marked block in parallel through `sandbox.exec(command, { cwd: sandboxRepoDir })` and replaces each block with the command's trimmed stdout (`src/PromptPreprocessor.ts:L23-L98`).
- Each expression has a 30 s timeout (`src/PromptPreprocessor.ts:L7`). A non-zero exit or a timeout fails the run (`src/PromptPreprocessor.ts:L55-L74`). ADR 0020 explains why expansion fails fast and never retries or degrades (`docs/adr/0020-prompt-expansion-fails-fast.md`).
- Expansion runs **once per iteration**, after the `onSandboxReady` hooks, so every iteration sees fresh context (`src/Orchestrator.ts:L406-L414`, `README.md:L583`). Inline prompts skip it (`skipPromptExpansion: isInlinePrompt`, `src/run.ts:L755`).

### 4. Injection into the agent process

- The orchestrator always calls `provider.buildPrintCommand({ prompt, dangerouslySkipPermissions: true, resumeSession, forkSession })` (`src/Orchestrator.ts:L141-L146`). It runs the resulting command with `stdin: printCmd.stdin` (`src/Orchestrator.ts:L188`).
- The sandbox contract says that when `stdin` is set, the string is piped to the child's stdin and the pipe is closed, which avoids the roughly 128 KB argv limit (`src/SandboxProvider.ts:L36-L45`). Docker adds `-i` and writes and ends stdin (`src/sandboxes/docker.ts:L261`, `src/sandboxes/docker.ts:L274-L276`). No-sandbox does the same (`src/sandboxes/no-sandbox.ts:L90-L92`).
- How each provider delivers the prompt:

| Provider | Print-mode command (hardcoded) | Prompt channel | Source |
| --- | --- | --- | --- |
| claude-code | `claude --print --verbose [--dangerously-skip-permissions\|--permission-mode X] --output-format stream-json --model M [--effort] [--resume id] [--fork-session] -p -` | stdin | `src/AgentProvider.ts:L1199-L1215` |
| codex | `codex exec [resume\|fork id] --json --dangerously-bypass-approvals-and-sandbox -m M [-c effort] [-]` | stdin | `src/AgentProvider.ts:L782-L814` |
| pi | `pi -p --mode json --model M [--thinking] [--session id]` | stdin | `src/AgentProvider.ts:L637-L654` |
| cursor | `agent --print --output-format stream-json --model M [--force] <prompt>` | argv (120 KiB guard) | `src/AgentProvider.ts:L124-L136`, `src/AgentProvider.ts:L848-L858` |
| opencode | `opencode run --format json --model M [--variant] [--agent A] [--dangerously-skip-permissions] <prompt>` | argv | `src/AgentProvider.ts:L967-L983` |
| copilot | `copilot -p <prompt> --output-format json --model M [--allow-all-tools] [--effort]` | argv `-p` (120 KiB guard) | `src/AgentProvider.ts:L1004-L1019`, `src/AgentProvider.ts:L1123-L1133` |

- Copilot details:
  - The code comment notes that Copilot can also read the prompt from stdin, but the provider uses `-p` for "parity with the tested print-command path" (`src/AgentProvider.ts:L1005-L1007`).
  - There is no option for a custom agent, and resume is disabled (`src/AgentProvider.ts:L1117-L1122`).
  - Interactive mode seeds the session with `-i`, not `-p` (`src/AgentProvider.ts:L1137-L1142`).
- Configurable per provider: the model is a factory argument. Effort, env, and the permission mode (Claude) or approvals reviewer (Codex) are options. OpenCode also accepts `agent` (`src/AgentProvider.ts:L946-L983`). Everything else on the command line is hardcoded.

### 5. Hardcoded text and conventions

- **Retry feedback.** When `output.maxRetries > 0`, Sandcastle resumes the session with a fixed message that ends "Emit only a corrected <tag> block. Do not change files or run commands." (`src/run.ts:L59-L88`, `src/run.ts:L866-L888`).
- **Completion signal.** The default is `<promise>COMPLETE</promise>`, and the run stops when any configured signal appears in the output (`src/Orchestrator.ts:L246`, `src/Orchestrator.ts:L332-L339`). The README says "the engine never injects it" (`README.md:L645`). Every scaffolded prompt therefore ends with that instruction, for example `src/templates/simple-loop/prompt.md:L53`.
- **Structured output.** The caller owns the instruction to emit the tag. Sandcastle only checks that the tag literal appears in the resolved prompt (`src/run.ts:L606-L614`, `CONTEXT.md:L106`).
- **`SKELETON_PROMPT`.** Defined at `src/templates.ts:L1-L26`, but no other non-test file under `src/` references it. The `blank` template ships an equivalent file at `src/templates/blank/prompt.md`.

### 6. Shipped default prompt files and how users override them

- **Templates.** Five ship under `src/templates/<name>/`: `blank`, `simple-loop`, `sequential-reviewer`, `parallel-planner`, and `parallel-planner-with-review` (`README.md:L752-L760`).
- **What a template contains.** Each holds `*.md` prompts and a `main.mts`. The planner templates add `plan-prompt.md`, `implement-prompt.md`, and `merge-prompt.md`, plus a review prompt and `CODING_STANDARDS.md` when review is included.
- **What `sandcastle init` does:**
  1. Copies every template file except `template.json`, `.env.example`, and compiled files into `.sandcastle/` (`src/InitService.ts:L729-L755`).
  2. Rewrites `claudeCode("…")` in `main.mts` to the selected agent and model (`src/InitService.ts:L764-L814`).
  3. Substitutes the issue-tracker `{{KEY}}`s into all text files (`src/InitService.ts:L875-L910`).
  4. Strips `--label Sandcastle` from the prompts if the user declined the label (`src/InitService.ts:L817-L845`).
  5. Refuses to run if `.sandcastle/` already exists (`src/InitService.ts:L1039`).
- **Two-stage templating.** For example, `` !`{{LIST_TASKS_COMMAND}}` `` in `src/templates/simple-loop/prompt.md:L5`:
  - At init, the placeholder is replaced with the tracker's literal command.
  - At run time, the resulting `` !`gh issue list …` `` block is expanded inside the sandbox.
- **Overriding.** Users edit the copied files in `.sandcastle/` directly. There is no layered fallback or merge with the defaults. Per run, they point `promptFile` at another file, pass `prompt` inline, or vary `promptArgs`. For example, the planner template passes `TASK_ID`, `ISSUE_TITLE`, and `BRANCH` to the implementer, and `BRANCHES` and `ISSUES` as markdown lists to the merger (`src/templates/parallel-planner-with-review/main.mts:L125-L135`, `src/templates/parallel-planner-with-review/main.mts:L208-L221`).
- **Project rules.** Templates reference `@.sandcastle/CODING_STANDARDS.md`, which relies on the agent CLI's own file-include. Sandcastle does not inline the file (`src/templates/parallel-planner-with-review/review-prompt.md:L41`).

### 7. Dogfood pattern (repo's own `.sandcastle/`)

- The repo's own `.sandcastle/agent-workflows/*` uses a `prompt.md` and `extraction.md` pair per workflow.
- `runWithExtraction` first runs the produce prompt. It then resumes the same session with an inline extraction prompt and `output` set (`.sandcastle/agent-workflows/shared/run-with-extraction.ts:L23-L52`).
- This separates the work instructions from the structured-output instructions.

## Implications for ralph dev

- **Hardcode only orchestration mechanics:** the `copilot -p` invocation and its flags (`--output-format json`, `--allow-all-tools`, `--model`, and the custom agent flag), completion-signal detection, branch-derived built-in variables, and any retry or feedback text. Keep all task instructions in files. Sandcastle shows that a tool can ship zero built-in task prose.
- **Put the stable instructions in the custom agent.** Sandcastle never touches agent-native channels. Put role, rules, and skill pointers in the Copilot custom agent and repo `AGENTS.md`/skills, and keep the per-task `-p` prompt thin: a task id, a branch, and an expanded context block. This also keeps the prompt under Copilot's argv limit; Sandcastle guards at 120 KiB (`src/AgentProvider.ts:L1011`).
- **Treat the per-task prompt as a user-overridable template file.** Ship a default and let `ralph dev` select it with a path option. Adopt Sandcastle's rules:
  - `{{KEY}}` substitution only for file-sourced prompts.
  - A missing key is an error.
  - Built-in keys cannot be overridden.
  - Argument values are inert, with no shell expansion of substituted content.
- **If dynamic context is needed** (an issue body, a diff), expand it fresh per task inside the worktree and fail fast on a non-zero exit or timeout rather than degrading (ADR 0020). Alternatively, fetch it in Python and pass it as an inert argument.
- **Make the completion and structured-output contract explicit.** Ralph's prompt or agent must tell Copilot to emit the signal or tag, and `ralph dev` should check that the resolved prompt contains it before running, as `src/run.ts:L606-L614` does.
- **Use argv `-p` unless prompts grow large.** Copilot does not support resume in Sandcastle, so the "resume plus feedback" retry pattern is unavailable. Prefer one-shot runs, and re-run with a corrective prompt if needed.

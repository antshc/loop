# Loop library feature parity

Checklist for [adopt-agent-builder-library.md](adopt-agent-builder-library.md): every old `src/loop` feature and where it goes. Tick `Done` as each phase lands.

Legend: Covered (prototype equivalent), Replaced (different solution), Missing -> workflow (re-implemented under `workflows/`), Dropped (removed by decision or unused).

| Done | Old feature (`src/loop`) | Status | Prototype equivalent or destination |
|---|---|---|---|
| [x] | `AgentRunnerProvider.create` (worktree + hooks + client) | Replaced | `Agent().with_git(...).open()` yielding a `Worktree` |
| [x] | `AgentRunner.run` (prompt, model, effort, options) | Replaced | `wt.agent(AgentProfile).run(AgentRequest)`; model and effort live on `AgentProfile` |
| [x] | `AgentRunner` as context manager | Replaced | `with builder.open() as wt:` |
| [x] | Runner-generated branch `loop/run-<id>` | Replaced | `MergeToHeadStrategy` temp branch or explicit `BranchStrategy` name |
| [x] | `AgentRunResult.commits`, `branch` | Missing -> workflow | `WorkflowGit.commits_since`; `AgentResult` has only output, session, exit code |
| [x] | `WorktreeLifecycle.begin_run` / `end_run` / `has_changes` | Missing -> workflow | `WorkflowGit.head`, `commits_since`, `is_clean` |
| [x] | Keep dirty worktree on cancel | Dropped | `BranchStrategy` safety-net commit |
| [x] | `Cancelled`, cancel `Event`, cancel-aware `execute` | Dropped | Decision: drop cancellation |
| [x] | `CommandExecutor` injection, streaming `on_line` | Dropped | `CliRunner` is the seam; no live streaming (`capture_output`) |
| [x] | `AgentOptions.timeout_s` | Dropped | No equivalent; hook timeouts only |
| [x] | `AgentClient` ABC + `AgentClientFactory` | Replaced | `AgentCli` protocol + `AgentProfile` + `CliAgentClient` |
| [x] | `CopilotClient` command flags (`-p`, `--allow-all-tools`, `--model`, `--reasoning-effort`, `--add-dir`, `--name`/`--resume`) | Covered | `CopilotCli.command` |
| [x] | Copilot `--output-format json` + `assistant.message_delta` reassembly | Missing -> workflow | Prototype returns raw stdout; workflow extracts the envelope from text |
| [x] | `CopilotOutputParser` (last `{status,...}` object, success from exit code + status) | Missing -> workflow | `workflows/dev/result.py`; non-zero exit raises `CalledProcessError` |
| [x] | `AgentOptions.agent`, `deny_tools`, `extra_args` (`--no-color`) | Replaced | `AgentProfile.args` |
| [x] | `AgentOptions.add_dirs` | Covered | `AgentContext.add_dirs` (not reachable through `Agent()`) |
| [x] | Harness-root agent cwd | Dropped | Agent cwd is the worktree (ADR records it) |
| [x] | `SessionStore`, `FileSessionStore`, `InMemorySessionStore` | Replaced | `SessionStore` keyed by `(SessionName, cli)`, `MemorySessionStore`; no file-backed store |
| [x] | `session_name_prefix`, `new_session=True` | Replaced | `SessionName.new()`, `with_session()` |
| [x] | `Prompt` (placeholders, warnings, errors, `` !`cmd` ``) | Missing -> workflow | `workflows/dev/prompting.py`; `` !`cmd` `` dropped |
| [x] | `extract_tag`, `extract_json` | Dropped | Unused by workflows; envelope scanner replaces them |
| [x] | `Git` facade, `BranchService`, `WorktreeService`, `CommitService`, `GitClient` | Replaced | `GitCli` + strategies in `loop.git` |
| [x] | `BranchService.prepare`, `fetch` | Covered | `BranchStrategy` (`fetch`, `add_worktree`) |
| [x] | `BranchService.can_prepare`, `ahead_of_remote`, `push` | Missing -> workflow | `WorkflowGit.remote_branch_exists`, `push_if_ahead` |
| [x] | `BranchService.merge`, `delete` | Covered | `MergeToHeadStrategy` |
| [x] | `WorktreeService.create` (reuse, replace clean leftover, refuse dirty) | Replaced | `GitCli.add_worktree` reuses an existing branch; no leftover handling |
| [x] | `WorktreeService.list`, `get`, `detach` | Dropped | Unused by workflows |
| [x] | `WorktreeService.has_changes`, `is_clean` | Missing -> workflow | `WorkflowGit.is_clean` |
| [x] | `WorktreeService.remove(force)` | Covered | `GitCli.remove_worktree` |
| [x] | `CommitService.head`, `since`, `find_since`, `restore` | Missing -> workflow | `WorkflowGit` equivalents |
| [x] | `CommitService.identity` | Dropped | Unused by workflows |
| [x] | `Branch` / `Worktree` / `Commit` entities | Missing -> workflow | `Commit` in `workflows/platforms/git.py` |
| [x] | `RepositoryData` | Missing -> workflow | `work_tracking/repository.py` |
| [x] | `Hook(command, timeout_s)` at `worktree-ready` | Replaced | `WorktreeReadyLoopHook` (+ `WorktreeRemovingLoopHook`, `RunFinishedLoopHook`) |
| [x] | `run_host_hooks`, `HookError` | Replaced | `GitCli.run_hook`, `LoopHookError` |
| [x] | `run_command`, `cli_runner`, `CommandResult`, `CommandError` | Missing -> workflow | `workflows/platforms/process.py` |
| [x] | `ExecutionStore`, `FileExecutionStore`, `InMemoryExecutionStore` | Missing -> workflow | `workflows/dev/store.py`; partial-ticket counters dropped |
| [x] | `configure_logging` | Missing -> workflow | `workflows/dev/logging_config.py` |
| [x] | `parallel_settled`, `Settled` | Dropped | Unused by workflows |
| [x] | `LoopError`, `AgentError`, `ExtractionError`, `PromptError`, `ExecutionStoreError` | Replaced | Local `DevError` family; prototype adds `SessionCliMismatch`, `SessionHandleMissing`, `UnsupportedAgentCliHookPoint` |
| [x] | `loop.testing` fakes (`FakeGit`, `FakeAgentClient`, `FakeCopilotCli`) | Dropped | Fake `CliRunner`, fake `GitService`, fake `WorkflowGit` in tests |
| [x] | Public-API guard tests | Dropped | Import-linter contracts |
| [x] | New in prototype only | New | Docker (`with_docker`), `CodexCli`, `AgentProfile`, git strategies, agent CLI hooks, dry-run adapters, `repo_agent`, `python -m loop --dry-run` |

## Workflow impact

| Workflow | Old capabilities used | New home |
|---|---|---|
| `dev` | runner provider, `Git`, `Prompt`, `ExecutionStore`, `Hook`, `configure_logging`, `LoopError`, `Cancelled`, `CommandExecutor` | builder, `WorkflowGit`, local helpers |
| `plan_implement` | runner, model/effort, `InMemorySessionStore`, `Git`, `RepositoryData` | builder, two `AgentProfile`s, `WorkflowGit` |
| `platforms/work_tracking` | `cli_runner`, `run_command`, `RepositoryData` | `workflows/platforms/process.py`, local `RepositoryData` |

## Open follow-ups

- `Agent()` does not expose `add_dirs`.
- No per-run timeout, no output streaming, no file-backed session store.

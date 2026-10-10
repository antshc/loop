# Plan: adopt the agent-builder library in `src/loop` and rewrite the workflows

Replace `src/loop` with a verbatim copy of `docs/prototypes/agent-builder/agent/`. Rewrite `workflows/` on the builder API. Re-implement inside `workflows/` what the prototype lacks. Update tests, import-linter contracts and docs.

## Decisions

- Tests: replace the old ones; no real-git tests (fakes only).
- Update `pyproject.toml` import-linter contracts and the docs (`ARCHITECTURE.md`, `CONTEXT.md`, ADRs, concepts, building block).
- Drop cancellation: no `Cancelled`, cancel `threading.Event`, `CommandExecutor`, or keep-dirty-worktree-on-cancel.
- Drop `--harness-root`; use `Agent()`, so the process cwd is the agent cwd.
- Keep both workflows (`dev`, `plan_implement`).
- `src/loop/__main__.py` (dry-run demo) comes along verbatim.
- The prototype directory stays in place.

## Phases

### Phase 1: library swap

1. Delete the old `src/loop/` contents (`testing/`, `contracts/`, `runs/`, `stores/`, `platforms/`, `parallel.py`, `logging_config.py`, ...).
2. Copy `docs/prototypes/agent-builder/agent/**` into `src/loop/` byte-for-byte, skipping `__pycache__`. Imports are relative, so no edits are needed. Public API: `Agent`, `AgentBuilder`, `AgentProfile`, `AgentRequest`, `GitOptions`, `BranchStrategy`, `copilot`, and the rest of `src/loop/__init__.py`.

### Phase 2: workflow-owned replacements (depends on 1)

3. `workflows/platforms/process.py`: `run_command`, `cli_runner`, `CommandError` (needed by `gh_client.py`). Move `RepositoryData` into `workflows/platforms/work_tracking/repository.py`.
4. `workflows/platforms/git.py`: `Commit` dataclass and `WorkflowGit` (injectable `run`): `fetch`, `remote_branch_exists`, `push_if_ahead(branch, base)`, `head`, `commits_since`, `initiative_commits(base, prefix)`, `restore` (reset --hard + clean -fd), `is_clean`.
5. `workflows/dev` additions:
   - `prompting.py`: `{{KEY}}` substitution; error on a missing argument, warning on an unused one. `` !`cmd` `` expansion is dropped (`dev.md` has none).
   - `result.py`: local `DevError` base instead of `LoopError`; port the "last JSON object carrying `status`" extraction (the prototype `CopilotCli` returns raw stdout).
   - `store.py`: `FileExecutionStore` trimmed to `record_failure`, `failed_attempts`, `reset`, daily file, clock seam.
   - `logging_config.py`: JSON-lines logging moved from the old library (`python-json-logger` stays a dependency).
   - `settings.py`: `HOOKS` becomes `LOOP_HOOKS: tuple[LoopHook, ...]`.

### Phase 3: `dev` rewrite (depends on 2)

6. `deps.py`, `__init__.py`, `workflow.py`: `DevDeps` carries `new_agent: Callable[[], AgentBuilder]` (default `Agent`), `WorkflowGit`, store, tracker, prompt. Remove `AgentRunnerProvider`, `agent_factory`, `executor`, `cancel`, `--harness-root`. `--log-dir` and `--log-level` stay.
7. Per Spec: `_prepare` fetches, checks `remote_branch_exists`, publishes earlier commits before the worktree opens, then calls `new_agent().with_git(GitOptions(root_path=repository.worktree_root, repository_path=repository.path, strategy=BranchStrategy(feature_branch, f"origin/{base}"), loop_hooks=LOOP_HOOKS)).open()`.
8. Inside the `with` block `wt.agent(DEFAULT)` creates one client; each attempt is `client.run(AgentRequest(rendered_prompt))` (stateless, so a fresh session per Ticket attempt).
   - `subprocess.CalledProcessError` becomes the failure reason "agent process did not exit successfully".
   - On failure, `WorkflowGit.restore(head_before)`.
   - Commit validation stays: exactly one commit, `ccode(...)` subject prefix, clean tree, reported SHA equals HEAD.
   - Publish (push + draft PR) from `wt.path` before leaving the block.
9. Reword `workflows/dev/prompts/dev.md`: remove "You start in the harness root ... `cd` to the worktree" (agent cwd is now the worktree).

### Phase 4: `plan_implement` rewrite (parallel with 3)

10. `Agent().with_git(GitOptions(..., BranchStrategy("loop/run-<hex>", "origin/main"))).open()`; `PLANNER = AgentProfile(copilot, PLAN_MODEL, PLAN_EFFORT)`, `IMPLEMENTER = AgentProfile(copilot, IMPLEMENT_MODEL, IMPLEMENT_EFFORT)`; run via `wt.agent(profile).run(...)`.
11. Catch `KeyboardInterrupt` inside the `with` block and return 130, so the safety-net commit and worktree removal still run (the prototype's removal fails on a dirty tree if the exception propagates). Log commits via `WorkflowGit.commits_since`.

### Phase 5: tests (depends on 3 and 4)

12. Delete tests of removed code: `test_agent_runner`, `test_agents`, `test_branch_service`, `test_commit_service`, `test_fake_git`, `test_git_client`, `test_git_facade`, `test_git_objects`, `test_worktree_service`, `test_process`, `tests/live/test_plan_implement_live.py`. Trim real-git helpers and `FakeRunner` from `tests/conftest.py`.
13. Move `docs/prototypes/agent-builder/test_agent.py` to `tests/unit/test_agent_builder.py`, changing only `agent` imports to `loop`.
14. Rewrite `tests/workflow_harness.py` around a fake `CliRunner` (`AgentBuilder(runner=..., git=FakeGitService, docker=DockerRuntime())`) and a fake `WorkflowGit` (in-memory commits/branches). Port the scenarios: ticket delivery, retry then escalation, git validation, response extraction, publication when the branch is ahead, repo-config errors, `plan_implement` model/effort per run. Add unit tests for the prompt renderer, `result.py`, `store.py`, `logging_config`. Keep and fix imports for the `gh_client`, `tracker`, `repository` tests.

### Phase 6: config and docs (depends on 1 and 2; parallel with 5)

15. `pyproject.toml` import-linter contracts for the vertical layout:
    - `loop.sessions` and `loop.hooks` import nothing else from `loop`.
    - Layer order: `builder`/`factory` -> `agents` -> `clis`/`git`/`docker` -> `run` -> leaves.
    - Workflows import only the public `loop` package.
    - Keep the independence contract for `workflows.dev` and `workflows.plan_implement`.
    - Remove the `loop.testing` contract.
16. Docs: update `ARCHITECTURE.md` (strategy table, Deployables row and trigger words, Codebase Structure), `CONTEXT.md`, `docs/building-blocks/loop.md`. Mark superseded ADRs (agent runner, harness root, split git services, output-parser streaming, model/prompt run arguments, pre-agent hooks). Record one new ADR for the builder/profile/strategy/session design. Revise concepts: `str-agent-client`, `str-agent-run`, `str-lifecycle-hooks`, `str-harness-repository-topology`, `str-loop-library-workflow-architecture`, `ops-test-strategy`.
17. Prune the obsolete repo memory note about `loop.worktrees` pytest quirks.

## Verification

1. `pip install -e ".[dev]"` and `pytest` from the repo root; the import-linter test passes.
2. `python -m loop --dry-run` runs the verbatim demo.
3. Workflows run only against fakes through the test harness; no real Copilot or git.
4. `grep` shows nothing in `workflows/` or `tests/` imports a removed name (`AgentRunnerProvider`, `loop.testing`, `Hook`, `ExecutionStore`, `cli_runner`, `RepositoryData` from `loop`).
5. No Pylance errors in the workspace.
6. Every old public name in `src/loop/__init__.py` `__all__` appears in [loop-library-feature-parity.md](loop-library-feature-parity.md); every `Missing -> workflow` row names a target file.

## Behavior changes and risks

- Agent cwd moves from the harness root to the worktree; this contradicts the ADR "Run Copilot CLI agents from the harness root". For a non-harness target repo the harness `.github` customizations are not visible, because `Agent()` does not expose `add_dirs`. The new ADR records this.
- `CopilotCli` returns raw stdout (no `--output-format json`), so the workflow extracts the response envelope from text.
- Live output streaming and per-run timeouts are lost.
- A non-zero agent exit now raises `CalledProcessError`.
- Existing local branches are reused as-is, not force-reset.
- An exception leaving `open()` with a dirty tree makes worktree removal fail.
- Cancellation is gone; only `plan_implement` handles `KeyboardInterrupt`.

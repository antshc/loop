# PR Babysitter: single process with threads

## Goal

Replace the PR babysitter's one-subprocess-per-PR execution with a single Python process using `ThreadPoolExecutor`. Keep PR discovery, concurrent execution, workflow behavior, and durable recovery unchanged.

## Constraints

| Requirement | Rationale |
| --- | --- |
| MUST run the scheduler and PR workflows in one Python process. | Remove subprocess supervision. |
| MUST retain configurable PR concurrency (`concurrency`, default 2). | Keep current throughput and backpressure. |
| MUST isolate ordinary exceptions per PR; one failure MUST NOT stop other PRs. | Preserve failure containment at the workflow level. |
| MUST preserve the Execution ledger, per-PR worktrees, locks, handoffs, and retry rules. | Prevent duplicate work or lost recovery state. |
| MUST preserve `--config`, `--pr`, `--include-existing`, and `--retry`. | No CLI migration. |
| MUST preserve `pr_babysit` Workflow definition, Stages, Flows, and agent roles. | Execution mechanism only. |
| MUST use cooperative shutdown; MUST NOT claim threads can be killed. | Python cannot forcibly stop a running thread. |
| MUST NOT introduce `asyncio`, a new queue, new dependencies, or a generic scheduler in the Loop library. | Existing Workflow runtime is synchronous; YAGNI, even if a broader redesign seems simpler. |
| MUST use separate per-PR services, runtime state, and telemetry context. | Avoid mutable cross-PR interference. |

## Existing implementation

- `workflows/software_factory/workflows/pr_babysit/intake.py`: `Scheduler` discovers PRs, enforces concurrency, and supervises `ProcessLauncher`/`Child`.
- `workflows/software_factory/workflows/pr_babysit/__main__.py`: `main()` selects discovery or `_maintain()`; `_maintain()` builds services, runs the Workflow, and handles handoff.
- `src/runtime/run.py` and `src/runtime/ledger.py`: already provide one run per work item with a per-PR ledger lock. No runtime changes required.
- `workflows/tests/software_factory/workflows/pr_babysit/test_intake.py`: discovery tests currently fake child processes.

## Implementation

1. **Reuse one-PR execution — `__main__.py`.**
   - Keep `_maintain` as the one-PR callable, accepting the shared stop request; move signal handling to the outer entry point so workers never install handlers.
   - Retain direct `--pr` execution through the same callable and preserve its integer exit-code result. Do not introduce a worker framework or a new result wrapper.
   - Build a fresh `Services`, `Runtime`, `Ledger`, `AgentLauncher`, observer/spans, and verification callable per PR. Do not share mutable workflow services.
   - Install SIGINT/SIGTERM handlers once, in the main thread only.

2. **Replace subprocess supervision — `intake.py`.**
   - Use `ThreadPoolExecutor(max_workers=settings.concurrency)`.
   - Track active `Future` objects by `owner/repo#number`.
   - Submit eligible PRs through the extracted one-PR callable; reap completed futures before each scan.
   - Preserve the registry baseline, open-by-author discovery, `--include-existing`, PR eligibility, per-PR locking, fingerprint release, and concurrency cap.
   - Remove `Child`, `ChildLauncher`, `ProcessLauncher`, `child_command`, `child_environment`, and their now-unused imports/arguments. Keep only a small callable seam if needed for deterministic tests.
   - Keep `subprocess` exception imports: discovery still handles `CalledProcessError` and `TimeoutExpired`. Keep the corresponding import-linter exception while that dependency remains.
   - Keep registry writes and active-future bookkeeping on the scheduler thread; workers own only their per-PR execution state.

3. **Preserve completion and failure policy.**
   - `_maintain` returns its existing integer exit code; normal completion, including handed-off/held results, remains governed by the existing ledger state.
   - An unexpected future exception is logged with PR identity; if its run is unclosed, mark the registry entry `crashed`. Do not rerun it automatically.
   - Preserve explicit `--retry` for crashed workers and fingerprint-based release of ordinary blocked PRs.
   - Always free the future's concurrency slot. Never run the same PR twice concurrently.

4. **Cooperative shutdown.**
   - Share a thread-safe stop signal with the scheduler and workers. A signal stops discovery and wakes poll waits.
   - In `finally`, request the shared stop on every scheduler exit path, including discovery errors, before joining the executor. Keep the scheduler lock until workers finish and reap completed futures before leaving.
   - Wait for in-flight workflow actions to return, then join the threads. Existing agent/check timeouts help, but do not guarantee a shutdown deadline: `src/git/git_cli.py::_invoke` currently has no timeout.
   - Document the intentional difference: there is no 10-second terminate/kill fallback; a stuck Python thread cannot be safely force-killed. Adding Git timeouts is a separate change.

5. **Telemetry lifecycle.**
   - Do not clear tracing environment variables process-wide. Give each worker an independent trace root/context.
   - Close and flush each owned per-PR observer in `finally`, including failure paths. Repeated PR runs must not accumulate exporter threads or providers until process exit.
   - If the current observer API cannot release owned providers, add the smallest lifecycle hook in `src/observe/otel.py` and expose it through `workflows/software_factory/infrastructure/telemetry.py`. Cover base trace/metric providers and lazily created role providers; do not shut down externally supplied providers.
   - Preserve the original workflow exception if telemetry cleanup fails. No generic telemetry redesign.

6. **Documentation and cleanup.**
   - Update `workflows/software_factory/workflows/pr_babysit/README.md` to describe thread workers, failures, and shutdown.
   - Leave `pyproject.toml` unchanged unless an import actually disappears. In particular, the `intake.py -> subprocess` exception remains required for discovery error handling.
   - Do not change the JSON config schema or stored scheduler/ledger formats; no state migration.

## Affected files (excluding tests)

| File | Change |
| --- | --- |
| `workflows/software_factory/workflows/pr_babysit/intake.py` | Futures, exception reaping, bounded submissions, cooperative shutdown. |
| `workflows/software_factory/workflows/pr_babysit/__main__.py` | Reuse `_maintain`, shared stop, main-thread signal handling, per-PR services and observer cleanup. |
| `workflows/software_factory/workflows/pr_babysit/README.md` | Thread execution, failures, shutdown limitations. |
| `docs/plans/pr-babysit-single-process-threads.md` | Plan and review corrections. |
| `src/observe/otel.py` | Minimal owned-provider cleanup hook if needed by the existing API. |
| `workflows/software_factory/infrastructure/telemetry.py` | Expose the observer lifecycle hook if needed. |

The execution change stays in two Python files plus the README. Telemetry lifecycle is the only planned supporting source change; no runtime, ledger, definition, Stage, agent, settings, or persistence changes. No new source module is required. Keep the existing mechanisms even if a broader redesign seems faster or simpler.

## Verification

Update scheduler, entry-point, and telemetry tests as needed. Use deterministic synchronization (`threading.Event`, not timing sleeps):

- Three eligible PRs and `concurrency=2` → at most two active runs; third starts after a slot frees.
- Same PR seen repeatedly → one active run.
- One worker raises an unexpected exception → another continues; incomplete work requires explicit retry.
- Normal held/blocked PR → existing fingerprint/retry rules unchanged.
- Two PRs from one repository → independent worktrees, services, and ledgers.
- Concurrent attempt on the same work item → ledger locking prevents duplicate execution.
- SIGINT/SIGTERM → stop discovery, notify workers, wait for cooperative completion.
- Unexpected scheduler failure → shared stop is requested before executor join; workers leave their poll loops and the scheduler lock is retained until they finish.
- `--pr` direct mode and `--include-existing` baseline remain compatible.
- Telemetry produces independent PR root spans without process-wide trace-context mutation.
- Repeated PR runs and failure paths → owned telemetry providers are flushed/closed, exporter threads do not accumulate, and externally supplied providers remain usable.

Run from repository root:

```sh
python3 -m pytest workflows/tests/software_factory/workflows/pr_babysit
python3 -m pytest
ruff check .
ruff format --check .
lint-imports
```

## Risks / trade-off

- **Thread termination:** Loss of force-kill isolation; shutdown has no guaranteed upper bound because some Git operations have no timeout. Agent/check timeouts bound only those operations. If a hard termination deadline is required, separate processes remain necessary.
- **Telemetry lifetime:** Process exit no longer cleans up each finished PR's exporters. Owned observer resources must be released explicitly.
- **Shared address space:** Fatal process crashes or native-library faults can affect every PR. Ordinary Python exceptions remain isolated at each future boundary.
- **Thread safety:** Verify `GitHub`, worktree operations, ledger locks, and telemetry under real concurrency; use per-PR instances where mutable state may exist.

## Out of scope

- Changes to Loop's Workflow runtime or `pr_babysit` definition.
- Changes to review/CI policy, agents, PR discovery filters, or persistence format.
- New generic Job/Scheduler framework.
- Adding or redesigning Git-operation timeouts.

## Acceptance

Existing behavior remains the same except for execution isolation and cooperative shutdown: one scheduler process, up to `concurrency` PR threads, separate workflow state per PR, and no subprocess workers.

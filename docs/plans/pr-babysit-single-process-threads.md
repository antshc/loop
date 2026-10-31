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

1. **Extract one-PR execution — `__main__.py`.**
   - Make the existing `_maintain` logic callable from a thread without installing signal handlers.
   - Retain direct `--pr` execution through the same callable.
   - Build a fresh `Services`, `Runtime`, `Ledger`, `AgentLauncher`, observer/spans, and verification callable per PR. Do not share mutable workflow services.
   - Install SIGINT/SIGTERM handlers once, in the main thread only.

2. **Replace subprocess supervision — `intake.py`.**
   - Use `ThreadPoolExecutor(max_workers=settings.concurrency)`.
   - Track active `Future` objects by `owner/repo#number`.
   - Submit eligible PRs through the extracted one-PR callable; reap completed futures before each scan.
   - Preserve the registry baseline, open-by-author discovery, `--include-existing`, PR eligibility, per-PR locking, fingerprint release, and concurrency cap.
   - Remove `Child`, `ChildLauncher`, `ProcessLauncher`, `child_command`, `child_environment`, and their now-unused imports/arguments. Keep only a small callable seam if needed for deterministic tests.

3. **Preserve completion and failure policy.**
   - A normal `RunResult`, including a handed-off/held result, remains governed by the existing ledger state.
   - An unexpected future exception is logged with PR identity; if its run is unclosed, mark the registry entry `crashed`. Do not rerun it automatically.
   - Preserve explicit `--retry` for crashed workers and fingerprint-based release of ordinary blocked PRs.
   - Always free the future's concurrency slot. Never run the same PR twice concurrently.

4. **Cooperative shutdown.**
   - Share a thread-safe stop signal with the scheduler and workers. A signal stops discovery and wakes poll waits.
   - Wait for in-flight workflow actions to complete or hit their existing timeout, then join the threads.
   - Document the intentional difference: there is no 10-second terminate/kill fallback; a stuck Python thread cannot be safely force-killed. The existing agent/check timeouts remain necessary.
   - Do not clear tracing environment variables process-wide. Give each worker an independent trace root/context.

5. **Documentation and cleanup.**
   - Update `workflows/software_factory/workflows/pr_babysit/README.md` to describe thread workers, failures, and shutdown.
   - Remove outdated process-specific import-linter exceptions in `pyproject.toml` only when unused.
   - Do not change the JSON config schema or stored scheduler/ledger formats; no state migration.

## Verification

Update `workflows/tests/software_factory/workflows/pr_babysit/test_intake.py` with deterministic synchronization (`threading.Event`, not timing sleeps):

- Three eligible PRs and `concurrency=2` → at most two active runs; third starts after a slot frees.
- Same PR seen repeatedly → one active run.
- One worker raises an unexpected exception → another continues; incomplete work requires explicit retry.
- Normal held/blocked PR → existing fingerprint/retry rules unchanged.
- Two PRs from one repository → independent worktrees, services, and ledgers.
- Concurrent attempt on the same work item → ledger locking prevents duplicate execution.
- SIGINT/SIGTERM → stop discovery, notify workers, wait for cooperative completion.
- `--pr` direct mode and `--include-existing` baseline remain compatible.
- Telemetry produces independent PR root spans without process-wide trace-context mutation.

Run from repository root:

```sh
python3 -m pytest workflows/tests/software_factory/workflows/pr_babysit
python3 -m pytest
ruff check .
ruff format --check .
lint-imports
```

## Risks / trade-off

- **Thread termination:** Loss of force-kill isolation; shutdown can wait up to an in-flight operation's timeout. If a hard termination deadline is required, separate processes remain necessary.
- **Shared address space:** Fatal process crashes or native-library faults can affect every PR. Ordinary Python exceptions remain isolated at each future boundary.
- **Thread safety:** Verify `GitHub`, worktree operations, ledger locks, and telemetry under real concurrency; use per-PR instances where mutable state may exist.

## Out of scope

- Changes to Loop's Workflow runtime or `pr_babysit` definition.
- Changes to review/CI policy, agents, PR discovery filters, or persistence format.
- New generic Job/Scheduler framework.

## Acceptance

Existing behavior remains the same except for execution isolation and cooperative shutdown: one scheduler process, up to `concurrency` PR threads, separate workflow state per PR, and no subprocess workers.

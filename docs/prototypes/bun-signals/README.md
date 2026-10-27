# Bun signal handling experiment (#121)

## Question

While an agent child process is running, does Ctrl-C / SIGTERM reach a Bun process, does its handler and `finally` cleanup run, and what happens to the child and grandchild?

## Method

`spike.ts` spawns a long-running `sleep` as a direct child, or as a grandchild via `sh -c "sleep N & wait"`. It spawns it in three ways: `Bun.spawnSync`, node `child_process.spawnSync`, and async `Bun.spawn`. It installs SIGINT/SIGTERM handlers (kill the child if known, then `process.exit(130|143)`) and has a `finally` that logs.

Launchers: `bun run spike` (package.json script), `bun spike.ts`, and a `bun build --compile` binary. Each runs under `setsid`. Signals SIGINT or SIGTERM are sent either to the bun process (the `bun run` wrapper for `bun-run`) or to the whole process group (`kill -SIG -pgid`, simulating terminal Ctrl-C). Observed 1.5 s after the signal.

Run: `./run.sh` (Linux, Bun 1.4.4; 72 cases, ~4 min). Output is saved to `out/matrix.txt` (gitignored). A copy of the matrix is in [`matrix.txt`](matrix.txt).

## Observation

Columns: handler fired / `finally` ran / child fate, all within 1.5 s of the signal. The launcher column made no difference: `bun-run`, `bun-file` and `compiled` behave the same, with one exception noted below. The table groups the 72 cases.

| Mode | Child | Signal to | Handler | finally | Child fate |
|---|---|---|---|---|---|
| bun-sync / node-sync | direct | pid (INT/TERM) | **no** (deferred) | no | **orphaned**, keeps running |
| bun-sync / node-sync | direct | pgroup (INT/TERM) | yes | yes | killed (by the group signal) |
| bun-sync / node-sync | grandchild | pid | **no** (deferred) | no | orphaned |
| bun-sync / node-sync | grandchild | pgroup INT | **no** | no | grandchild orphaned (see below) |
| bun-sync / node-sync | grandchild | pgroup TERM | yes | yes | killed |
| bun-async | direct | pid / pgroup | yes | no (`process.exit` in handler skips it) | killed (`child.kill()` or group) |
| bun-async | grandchild | pid / pgroup INT | yes | no | **grandchild orphaned** (`kill()` hits only `sh`) |
| bun-async | grandchild | pgroup TERM | yes | no (yes for `bun-file`) | killed |

Notes:
- For sync spawns the signal is delivered but the JS handler does not run until the sync call returns. In the harness the exit code was 130/143 only after the child was force-killed, which shows the handler ran late. If the child never exits, the handler never runs.
- Group-INT with a grandchild: a non-interactive `sh` runs `cmd &` with SIGINT ignored, so the grandchild survives Ctrl-C. `sh` dies and the sync call stays blocked until the grandchild's stdout pipe closes.
- Group-TERM exit codes show `?` because the harness's own wrapper `bash` is in the group and is killed too.
- `bun run` forwards SIGINT/SIGTERM sent to the wrapper to the script. Async handlers fired when only the wrapper pid was signalled. The wrapper added no extra orphaning.
- Compiled binaries behave identically to `bun file.ts`.

## Conclusion

- **Sync vs async API:** a sync spawn blocks the event loop, so a signal handler cannot run until the child exits. Cancellation and cleanup are impossible with it, and an interrupted parent leaves the child orphaned if only the parent is signalled. The agent-running API should be async (`Bun.spawn`) so handlers run during the child.
- **Cancellation:** the handler must kill the child explicitly (`child.kill()`) and then await exit and run cleanup itself. Calling `process.exit` in the handler skips `finally`, so do cleanup inside the handler or convert the signal into an abort that unwinds normally. For grandchildren, `child.kill()` is not enough. Spawn in its own process group and kill the group (`process.kill(-pid)`), or the grandchild is orphaned. Not tested here: `detached` spawn and group kill.
- **Compiled binary distribution:** `bun build --compile` binaries have the same signal semantics as `bun file.ts` and `bun run`, so no extra handling is needed for distribution. Do not rely on `bun run` for cancellation tests. Signals sent to the real pid behave the same.

## Evidence

`spike.ts`, `run.sh`, `package.json`, and the full 72-row [`matrix.txt`](matrix.txt).

## Cleanup

No stray `sleep`/bun processes remain. `out/`, the compiled `spike-bin` and `node_modules/` are removed and gitignored. Nothing outside this directory was touched.

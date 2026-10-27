# Bun detached group-kill experiment (#123)

## Question

Under Bun 1.4.4 (`bun file.ts` and a `bun build --compile` binary), does a process helper that spawns the agent CLI `detached`, listens for SIGINT/SIGTERM while it runs, kills the agent's process group on a signal (SIGTERM, then SIGKILL after a grace period), and rejects with `Cancelled` meet all of these?

- (a) It kills the whole agent tree, including `sh -c` grandchildren and a child that ignores SIGTERM.
- (b) `finally` cleanup completes.
- (c) The process exits with 130 (SIGINT) or 143 (SIGTERM) when `Cancelled` reaches the `import.meta.main` guard.
- (d) A second signal force-exits.

## Method

In `spike.ts`, `runAgent()` calls `Bun.spawn(cmd, { detached: true })`, which supports `detached`, so the node `child_process` comparison was skipped. `runAgent()` adds the signal listeners. The first signal sends `process.kill(-pid, "SIGTERM")` and arms a 1 s SIGKILL timer. Then the helper awaits `exited` and throws `Cancelled`. A second signal SIGKILLs the group and calls `process.exit(128+n)`. `main()` creates a fake worktree dir and hook file and removes them in `finally`, after an optional delay. The `import.meta.main` guard maps `Cancelled` to `process.exit(130|143)`.

Agent kinds:
- `direct`: `sleep`.
- `grandchild`: `sh -c "sleep & wait"`.
- `stubborn`: the same command after `trap '' TERM`. Both `sh` and `sleep` ignore SIGTERM, so only the SIGKILL escalation stops them.

Each case runs under `setsid -f -w`, so Bun leads its own isolated group and its exit code is captured. One second after start, the harness sends SIGINT or SIGTERM to the Bun pid or to Bun's group (`kill -SIG -pgid`, simulating Ctrl-C). Second-signal cases send the signal to the pid again 0.5 s later, during a 3 s `finally`, or 0.3 s later, during the SIGKILL grace period.

Run: `./run.sh` (Linux only; 32 cases, about 2 min). The saved output and sample logs are in [`matrix.txt`](matrix.txt).

## Observation

`bun-file` and `compiled` rows were identical, and pid and pgrp delivery behaved the same. `tree` means any `sh` or `sleep` left after the Bun process exited. `files` means the fake worktree and hook file.

| Kind | Signal | 2nd signal | Escalated | finally done | Guard | Exit | Tree | Files |
|---|---|---|---|---|---|---|---|---|
| direct / grandchild | pid or pgrp, INT | — | no | yes | yes | 130 | dead | removed |
| direct / grandchild | pid or pgrp, TERM | — | no | yes | yes | 143 | dead | removed |
| stubborn | pid or pgrp, INT / TERM | — | **yes** (SIGKILL after 1 s) | yes | yes | 130 / 143 | dead | removed |
| grandchild | pid INT / TERM | during slow `finally` | no | **no** | no | 130 / 143 | dead | **left** |
| stubborn | pid INT / TERM | during grace period | no | **no** | no | 130 / 143 | dead | **left** |

Notes:
- `Bun.spawn({ detached: true })` calls `setsid()`, so the agent gets its own session and group (pgid = sid = child pid). Ctrl-C to Bun's group therefore never reaches the agent, and the helper alone owns its termination.
- The agent's `sh` (non-interactive, `cmd &`) ignores SIGINT. The helper sends SIGTERM to the group, not the received signal, so that ignored SIGINT does not matter.

## Conclusion

The detached-group design works on Linux for both launchers.

- **(a) Pass.** `process.kill(-pid, "SIGTERM")` kills the direct child and `sh -c` grandchildren. The SIGKILL escalation after the grace period kills SIGTERM-ignoring trees. The prior bun-signals experiment found grandchildren orphaned by `child.kill()`; that did not happen here.
- **(b) Pass.** Do not call `process.exit` in the handler. Have the helper reject after `exited`, and `finally` (worktree removal, hook deletion) runs to completion before exit.
- **(c) Pass.** `Cancelled` caught at the `import.meta.main` guard gives exit code 130 or 143. `import.meta.main` is true in the compiled binary as well.
- **(d) Pass, with a known trade-off.** A second signal SIGKILLs the group and exits immediately with 130 or 143, but skips the remaining cleanup and leaves the worktree and hook file behind.

Recommended design for Loop's process helper:
1. Use async `Bun.spawn(cmd, { detached: true })` only.
2. Add the SIGINT and SIGTERM listeners only while children run.
3. On the first signal, SIGTERM the group (`-pid`), arm a SIGKILL timer for the grace period, await `exited`, clear the timer, and throw `Cancelled(signal)`.
4. Keep a force-exit listener installed until the process exits. It SIGKILLs the group, then calls `process.exit(128+n)`.
5. Map `Cancelled` to `process.exit(130|143)` only in the `import.meta.main` guard.
6. Make cleanup idempotent so a later run can recover leftovers from a force-exit.

macOS and Windows are untested. Windows has no POSIX process groups or signals, so it needs a separate mechanism, such as `taskkill /T` or job objects.

## Evidence

`spike.ts`, `run.sh`, and [`matrix.txt`](matrix.txt), which holds the 32-row matrix and logs for grandchild, stubborn, and both second-signal cases.

## Cleanup

No stray `sleep`, `sh`, or bun processes remain; leftover processes were killed by PID. `out/` and the compiled `spike-bin` were removed and are gitignored. Nothing outside this directory was touched.

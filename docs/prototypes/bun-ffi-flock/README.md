# bun:ffi flock experiment (#125)

## Question

Under Bun 1.4.4 (`bun <file>` and a `bun build --compile` binary), can a `bun:ffi` binding to libc `flock(fd, LOCK_EX|LOCK_NB)`, called on an fd from `fs.openSync(path, "a+")`, replace Python's `process_lock` (`workflows/pr_babysit/infrastructure/state.py`)? It must do three things:

1. Exclude a second process, failing with a readable `EWOULDBLOCK`.
2. Release the lock when the holder is SIGKILLed.
3. Not leak the lock to children started with `Bun.spawn`.

## Method

`spike.ts` uses `dlopen("libc.so.6")` to bind `flock` and `__errno_location`. It reads errno with `read.i32(ptr)` right after a failed call. It has three modes:

- `hold`: lock the file, report the fd's `O_CLOEXEC` bit from `/proc/self/fdinfo`, and optionally `Bun.spawn` a `sleep 300` child.
- `try`: make one attempt. If it fails, make 10 000 more contended attempts and count the errno values. Then probe `flock(-1)` and expect `EBADF` (9).
- `same`: open the same file twice in one process and lock both fds.

`./run.sh` builds the compiled binary and runs every case under both launchers. For check 3 it lists `/proc/<child>/fd`, SIGKILLs the holder, and tries to lock while the child is still running. Output is written to `out/evidence.txt`; a copy is in [`evidence.txt`](evidence.txt).

Environment: Linux (WSL2 6.6), glibc 2.39, Bun 1.4.4.

## Observation

| Check | bun-file | compiled |
|---|---|---|
| compiled binary dlopens `libc.so.6` | n/a | ✅ |
| 1. second process excluded | ✅ rc −1 | ✅ rc −1 |
| 1. errno = EWOULDBLOCK (11) | ✅ 10 000/10 000 | ✅ 10 000/10 000 |
| 1. errno probe `flock(-1)` → EBADF (9) | ✅ | ✅ |
| 2. lock released on holder SIGKILL | ✅ new process acquires | ✅ |
| 3. `fs.openSync` fd has `O_CLOEXEC` | ✅ yes | ✅ yes |
| 3. child holds lockfile fd | ✅ no (only 0–2 → /dev/null) | ✅ no |
| 3. third process acquires while orphaned child runs | ✅ | ✅ |
| same process, second open+flock | blocked (EWOULDBLOCK) | blocked |
| same process, relock same fd | succeeds (no-op) | succeeds |

macOS and Windows were not tested. macOS needs `libc.dylib` (or `libSystem.B.dylib`), and errno is read through `__error` instead of `__errno_location`. `LOCK_EX`=2 and `LOCK_NB`=4 are the same on both, but `EWOULDBLOCK` is 35 on macOS and 11 on Linux. Windows has no `flock` and needs `LockFileEx` or another mechanism.

## Conclusion

On Linux, `bun:ffi` `flock` is a working implementation of Loop's lock adapter, with the same semantics under `bun file.ts` and the compiled binary. No pidfile fallback is needed.

- **Errno:** reading `__errno_location` right after the call returned the correct value every time across 10 000 calls. Treat any non-zero return as Blocked only when errno is `EWOULDBLOCK`, and use the platform's constant.
- **Crash and child safety:** the kernel releases the lock when the holder dies. Bun opens files with `O_CLOEXEC`, so `Bun.spawn` children never inherit the lock fd.
- **Same-process use:** flock applies per open file description, so a second open in the same process is refused. The adapter must keep one fd per lock and not reopen it.
- **Portability:** the adapter needs a macOS branch (library name, `__error`, errno 35). Windows needs a separate implementation.

## Evidence

`spike.ts`, `run.sh`, and [`evidence.txt`](evidence.txt).

## Cleanup

All holders and `sleep` children were killed by PID, and no stray processes remain. `out/` and `spike-bin` are gitignored. Nothing outside this directory was touched.

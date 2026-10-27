## Report: Bun parity for Python runtime behaviors (antshc/loop#110)

**Scope note:** these are Linux/macOS-oriented findings (Bun on Windows has documented extra gaps, noted where relevant). This is sourced from Bun docs and public GitHub issues as of late 2026; verify against the specific Bun version pinned for the port.

### Parity table

| # | Python behavior | Bun/TS equivalent | Verdict |
|---|---|---|---|
| 1 | `subprocess.run(check, capture_output, text, timeout, env, cwd)` | `Bun.spawnSync` / `Bun.spawn` | Adapt |
| 2 | `Popen` detached fire-and-forget | `Bun.spawn({..., stdio:"ignore"}).unref()` | Adapt |
| 3 | `signal.signal(SIGINT/SIGTERM, ...)` | `process.on('SIGINT'|'SIGTERM', ...)` | Adapt (caveats) |
| 4 | `ContextVar` for phase stack | `AsyncLocalStorage` (`node:async_hooks`) | Keep, with care |
| 5 | `fcntl.flock(LOCK_EX|LOCK_NB)` | No native equivalent; `fs.open(..., 'wx')`/`O_EXCL` pidfile, or npm `proper-lockfile` | Drop flock; adapt to O_EXCL/mkdir lock |
| 6 | `NamedTemporaryFile` + `os.fsync` + `os.replace`; `path.unlink(missing_ok=True)` | `fs.writeFileSync` to temp + `fs.fsyncSync` + `fs.renameSync`; `fs.rmSync({force:true})` | Keep (straightforward) |

---

### 1. subprocess.run → Bun.spawnSync/spawn

Python: `src/loop/clis/runner.py:44` (`self._run(argv, cwd=..., check=True, capture_output=True, text=True, **extra)` with `extra["timeout"]`, `extra["env"]={**os.environ,...}`); `src/loop/git/git_cli.py:103-117` (`run_hook`, `shell=True`, `timeout=hook.timeout_sec`, catches `subprocess.TimeoutExpired`); `src/loop/git/git_cli.py:150` (`self._run(command, check=check, capture_output=True, text=True)`).

Bun ships `Bun.spawnSync(cmd, opts)` (blocking) and `Bun.spawn` (async, promise-based `exited`), both documented at bun.com/docs/runtime/child-process. Key gaps:
- **Timeout/kill signal**: Bun supports `timeout` (ms) and `killSignal` (default `"SIGTERM"`) directly on both `spawn`/`spawnSync` — closer to Node's `child_process` than Python's bare `TimeoutExpired`. No built-in `TimeoutExpired`-style exception: you must inspect `result.signalCode`/`proc.signalCode` after timeout kill, not catch an exception — requires custom error-mapping code to reproduce `LoopHookError(... "timed out after ...")` from `git_cli.py:117`.
- **Nonzero exit / `check=True`**: Bun has no `check` option; must manually test `exitCode !== 0` and throw, replicating `CalledProcessError` semantics yourself (as already abstracted via the injectable `_run` callable in Python, so port is mechanical).
- **UTF-8 decoding of invalid bytes**: Python's `text=True` uses the locale decoder which can raise/replace depending on settings; Bun's `stdout`/`stderr` as `Buffer`/`ArrayBuffer` must be explicitly `.toString("utf-8")`, which (like V8) replaces invalid sequences with U+FFFD rather than erroring — behavior is similar but not identical to Python's strict-by-default decode; CLI output mangling is a plausible regression source, worth a conformance test.
- **Child process group killing**: Bun's `timeout`/`kill()` sends the signal to the *subprocess itself*, not automatically to a process group unless you set `detached: true` and manage `-pid` yourself (Unix). Python subprocess by default also only signals the direct child; group-kill is opt-in (`preexec_fn=os.setsid`) in both, so parity is roughly equivalent for current loop code, but if hooks shell out to multi-process pipelines, orphaned grandchildren are an equal risk in both runtimes.
- Source: bun.com/docs/runtime/child-process (fetched 2026; "Exit handling", "Resource usage" sections); community confirmation of `timeout`/`killSignal` semantics (bun.com/reference/bun/Spawn/SpawnSyncOptions/timeout).

**Recommendation**: Adapt. Wrap `Bun.spawnSync`/`spawn` in a small internal helper mirroring the Python `_run: Callable[..., CompletedProcess]` seam (both `runner.py:26` and `git_cli.py:19` already inject `run`, making this a drop-in shim) that normalizes `{stdout, stderr, returncode}` and raises on `check`/timeout to preserve call-site logic (`runner.py:44`, `git_cli.py:103-123`).

### 2. Popen detached fire-and-forget

Python: `workflows/pr_babysit/infrastructure/scheduler.py:28-32` (`child_command`), `:37` (`spawn=subprocess.Popen` injected), `:68` (`children[key] = spawn(child_command(...), env=child_environment())`), lifecycle managed with `child.poll()`, `child.terminate()`, `child.wait(timeout=10)`, `child.kill()` (`:69-76, 90-97`).

Bun's `Bun.spawn` returns a `Subprocess` with `.exited` (Promise), `.exitCode`, `.signalCode`, `.kill(signal)`, and `.unref()` to detach from the parent's lifetime so the parent can exit independently (bun.com/docs/runtime/child-process, "Exit handling"). There is no direct `.poll()` (non-blocking check); use `proc.exitCode !== null` or race `.exited` against a timeout to emulate polling. `.kill()` is non-blocking like `terminate()`; waiting equivalent to `child.wait(timeout=10)` must be implemented by racing `proc.exited` with `setTimeout`/`AbortSignal.timeout`.

**Recommendation**: Adapt. The scheduler's `spawn` callable is already an injectable dependency (`scheduler.py:37`), so the TS port needs a thin polyfill module exposing `.poll()`-like semantics over `Bun.spawn`, plus manual timeout-race logic for `wait(timeout=...)`.

### 3. SIGINT/SIGTERM handling

Python: `workflows/pr_babysit/__main__.py:27-34` (`signal.signal(SIGTERM, request_stop)`, `signal.signal(SIGINT, request_stop)`); cooperative-stop flag checked in `scheduler.serve`'s loop (`scheduler.py:40` `while not stop():`) and a direct `raise KeyboardInterrupt` path for single-PR mode.

Bun supports `process.on('SIGINT', ...)`/`process.on('SIGTERM', ...)` (Node-compatible API) but with **documented reliability gaps**, especially relevant to a CLI that also spawns children:
- `oven-sh/bun#11400` — SIGINT handler aborted when run via `package.json` script.
- `oven-sh/bun#13040` — `process.on("SIGINT")` invalid on Windows.
- `oven-sh/bun#15878` — Bun exits "hastily" under `bun start`.
- Community repro `Hona/bun-sigint`: confirms that `bun run <script>` forwards Ctrl-C to the whole process group and the **parent process exits before its own SIGINT handler runs**, and that this is *worse* when the script itself spawns child processes — the parent's handler "never even starts." Directly invoking the compiled binary (`bun build --compile`) avoids the bug because there's no intermediary `bun run` parent.
- No clear documentation/confirmation on whether a handler set with `process.on('SIGINT')` fires while Bun is blocked inside a synchronous `Bun.spawnSync` call (analogous to Python signal delivery being deferred until the C call returns); Node's behavior here is that signal handlers run on the next event-loop tick, so a `spawnSync`-equivalent blocking call would likely delay handler execution similarly to Python — not verified in Bun specifically.

**Recommendation**: Adapt, but **run the shipped CLI only as a compiled binary** (`bun build --compile`) or invoke the `.ts` entry directly with `bun <file>` rather than via `bun run`/`package.json scripts`, to avoid the group-SIGINT swallowing bug — this materially affects `pr_babysit/__main__.py`'s stop-flag design since `scheduler.py`'s loop assumes `stop()` is reliably observed. Add an integration test exercising Ctrl-C with an active child (`scheduler.py:66-76`) before relying on this in production.

### 4. ContextVar → AsyncLocalStorage

Python: `src/loop/workflow/phases.py:73` (`self._parent: ContextVar[Phase | None] = ContextVar(...)`), `:91` (`parent = self._parent.get()`), `:95` (`token = self._parent.set(phase)`), `:118` (`self._parent.reset(token)`) — used inside a sync `@contextmanager` generator wrapping both sync and (via `CancelledError`/`AsyncCancelledError` handling at `:108`) async call sites, i.e., truly dual-mode.

Bun implements Node's `node:async_hooks` `AsyncLocalStorage`, and it's documented/tested as part of Bun's Node compatibility layer. It correctly tracks context across `await` boundaries and callback chains in the common case. However:
- `AsyncLocalStorage.run()` is the idiomatic replacement for `ContextVar.set()`+manual `reset()`; Bun (like Node) does not expose a raw get/set/reset primitive with the same ergonomics as `ContextVar.set()` returning a token — you must restructure `_open`'s generator (`phases.py:88-120`) into an `als.run(phase, () => { ... })` callback form, which doesn't trivially nest inside a Python-style `@contextmanager`/JS generator-based context manager; this is a structural rewrite, not a line-for-line port.
- Known rough edges historically reported against Bun's AsyncLocalStorage involve interaction with microtask ordering around `fetch`/timers and certain third-party instrumentation libraries losing context across native callback boundaries — exercise targeted tests around the phase-stack nesting in `phases.py:91-100` (parent inheritance of `attributes`) specifically across awaited phases.

**Recommendation**: Adapt (not a 1:1 keep) — re-architect `Phases._open` around `AsyncLocalStorage.run()` rather than trying to preserve the token-based `set`/`reset` pattern; write unit tests asserting correct parent/child attribute inheritance (`phases.py:92-93`) across both sync and async phase bodies before trusting it in the ported scheduler, which runs phases inside a long-lived loop.

### 5. fcntl.flock → Bun locking

Python: `workflows/pr_babysit/infrastructure/state.py:45-67` (`process_lock`): opens `path` with `"a+b"`, uses `fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)` on POSIX (raises `OSError`→`Blocked` on contention) or `msvcrt.locking(..., LK_NBLCK, 1)` on Windows; lock auto-releases on process death (fd closed by OS). Used for both `scheduler.lock` (`scheduler.py:38`) and per-PR locks (`scheduler.py:66`).

Bun has **no built-in `flock(2)` binding** and no documented equivalent to Python's `fcntl.flock`. Options, with tradeoffs:
- Raw `O_CREAT|O_EXCL` pidfile lock via `fs.openSync(path, 'wx')` — but Bun has a confirmed bug (tracked via `garrytan/gstack#599`) where **numeric** open-flag combinations (`fs.constants.O_CREAT|O_EXCL`) fail with `ENOENT` specifically in **compiled** Bun binaries on Windows; the documented workaround is to use the **string** flag `'wx'` instead of numeric flags. Since item #3's recommendation is to ship a compiled binary, this bug is directly relevant — numeric flags must be avoided.
- `O_EXCL`/`wx`-based locks are not auto-released on crash the way `flock` is (stale lock files can persist after a hard kill); this is a real behavior regression from the Python version's crash-safety guarantee (`state.py:46` comment: "released automatically when a process dies") and must be compensated with staleness detection (PID + liveness check, or mtime-based staleness as used by `proper-lockfile`).
- `proper-lockfile` (npm) deliberately avoids both `flock` and `O_EXCL` in favor of atomic `mkdir`, specifically because `O_EXCL` is unreliable on network filesystems; it relies only on `fs.mkdir`, which Bun implements for Node compatibility, so it's a credible drop-in dependency, at the cost of pulling in a third-party package rather than stdlib-equivalent behavior.
- `bun:ffi` could theoretical bind libc `flock()` directly for POSIX, but this reintroduces native/platform-specific code (no Windows equivalent) and was not found as a known pattern in Bun's ecosystem/issue tracker — effectively unexplored territory, high risk for a "big-bang" port deadline.

**Recommendation**: Drop raw `fcntl.flock` parity; **adapt** to either (a) `proper-lockfile` with staleness/mtime checks reimplementing the "released on process death" guarantee via PID-liveness probing, or (b) a custom `O_EXCL`-with-string-flags pidfile lock plus explicit stale-lock recovery logic. Either way, `process_lock`'s crash-safety semantics (`state.py:41-67`) require new code, not a direct API swap — flag this as the highest-risk item in the parity assessment, since both `scheduler.lock` and per-PR locks (`scheduler.py:38,66`) gate correctness of the whole multi-process scheduler.

### 6. Atomic temp-file write + rename; recursive rm

Python: `workflows/pr_babysit/infrastructure/state.py:28-39` (`write_json`): `NamedTemporaryFile(dir=path.parent, delete=False)` → `json.dump` → `stream.flush()` → `os.fsync(stream.fileno())` → `os.replace(temporary, path)`, with `finally: temporary.unlink(missing_ok=True)` cleanup if `os.replace` never ran.

Bun supports Node's `fs`/`node:fs` API directly (`fs.writeFileSync`, `fs.fsyncSync`, `fs.renameSync`, `fs.rmSync({force: true})`, `fs.mkdtempSync`/manual random-suffix temp names since there's no direct `NamedTemporaryFile` equivalent — must manually generate a unique filename in the target directory, e.g. `${path}.${randomUUID()}.tmp`). `fs.renameSync` on the same filesystem is atomic on POSIX (same guarantee as `os.replace`); Bun doesn't change this OS-level behavior. `fs.rmSync(tmpPath, {force: true})` is the direct analogue of `unlink(missing_ok=True)`. No known Bun-specific gaps found for this item in docs or issue search.

**Recommendation**: Keep — straightforward mechanical port; just hand-roll the "create exclusive temp file in target dir" step since Bun lacks a `NamedTemporaryFile(dir=...)` convenience (use `fs.openSync(tmp, 'wx')` + string flag, consistent with the #5 Windows-compiled-binary caveat).

### Gaps / follow-ups
- Not verified: Bun signal-handler delivery timing specifically *during* a blocking `Bun.spawnSync` call (only inferred from Node-like event-loop semantics).
- Not found: any Bun-native `flock(2)` binding or first-party "advisory lock" API in Bun docs as of this search.
- Recommend a short spike: compiled-binary (`bun build --compile`) smoke test of Ctrl-C with an active `Bun.spawn` child, and an `O_EXCL`/`'wx'` pidfile race test on the target deployment OS, before committing to the big-bang port.
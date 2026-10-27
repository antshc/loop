# Port Loop and Workflows to TypeScript on Bun in one cutover

> Supersedes [Install Loop with pip from the repository](install-loop-with-pip-from-the-repository.md).

Loop and its example Workflows (`dev`, `pr_babysit`, `plan_implement`, `workflow_reporting`) were Python, installed with `pip`. The team wants them in TypeScript run by Bun without breaking what Workflow authors and operators rely on: the authoring API shape, saved state, the hook contract, log and trace formats, command-line flags, and the architecture boundaries. Loop and all four Workflows are ported in a single cutover, the Python sources are removed in the same change, and the repository revision still defines the delivered version.

- **Runtime.** Bun only; Bun APIs may be used anywhere. Node.js is unsupported, and every Workflow entry point refuses to run under it. The minimum Bun version is declared in `package.json` (`engines`, `packageManager`) and enforced at startup by `enforceRuntime`.
- **Delivery.** TypeScript source is consumed directly with no build step. Loop is a Bun package installed from this repository, pinned by revision and lockfile, whose single public entry point is `src/loop/index.ts`. It still ships no command and no built-in Workflows ([Ship Loop as a TypeScript workflow library](ship-loop-as-a-typescript-workflow-library-with-no-built-in-workflows.md)).
- **Entry points.** A Workflow runs as `bun workflows/<name>/main.ts` (or `bun workflows/<name>.ts` for one file) through the user's own alias, from the harness root. Each Workflow parses its own command line with `commander`; flags, defaults, and exit codes are unchanged.
- **Behavior is preserved.** The same class, builder, and error names with members in camelCase; the same persisted and emitted strings (hook points, state files, log records, span and metric names, hook payload). Everything that starts a process or does I/O is `async`; builders, profiles, and value objects stay synchronous. Worktrees, runs, and phases are callback scopes that return the body's result.
- **Cancellation.** SIGINT and SIGTERM raise an internal `Cancelled` error: agent children run in their own process group, receive SIGTERM and then SIGKILL after a grace period, the active phase is `cancelled`, worktree cleanup runs, and the process exits 130 or 143. A second signal force-kills the remaining groups and exits at once.
- **Locking.** `pr_babysit`'s single-instance locks are non-blocking OS locks through Bun's FFI (`flock` on POSIX, `LockFileEx` on Windows) and are released when the holder dies.
- **Telemetry.** The OpenTelemetry packages are optional peer dependencies, loaded by a guarded dynamic import only inside the OTLP observer; a missing package fails startup with an install message ([Observe Agent Runs through an optional observer seam](observe-agent-runs-through-an-optional-observer-seam-that-exports-to-opentelemetry.md)).
- **Parity gate.** The golden fixtures captured from the Python implementation (PR state, scheduler registry, dev execution log, settings, Copilot session log, event streams, log records, span and metric names and attributes, hook payload) are the parity gate; `tests/parity/golden-fixtures.test.ts` fails when a category loses its fixture or its fixture-backed test. No side-by-side Python replay runs after cutover, and Python stays recoverable through git history.
- **Quality gates.** One root command (`bun run test`) runs ESLint, the TypeScript compiler, and `bun test`, which includes the dependency-cruiser architecture contracts ([Test Strategy](../concepts/ops-test-strategy.md)).

## Considered Options

- **Keep Python and add a TypeScript sibling** — rejected: two implementations of one behavior drift apart, and the team wants a single runtime.
- **Incremental port with Python/TypeScript interop** — rejected: an interop seam is a third system to maintain; a big-bang cutover with no coexistence period is simpler.
- **Support Node.js as well as Bun** — rejected: Bun supplies process spawning, FFI locking, and TypeScript execution without a build step; supporting two runtimes widens the compatibility surface.
- **Publish a built package to a registry** — rejected for the same reason the pip index was: the repository revision is the version, and a build step would be new machinery.

## Consequences

- Users need Bun at or above the declared minimum and update by moving the pinned revision; there is no package index.
- The suite runs on the minimum Bun and on the latest Bun; Python-version compatibility testing is gone.
- Other ADRs and Concepts that named Python-specific constructs (`abc.ABC`, `CalledProcessError`, `pytest`, `import-linter`, `pip install loop[otel]`, `snake_case` members) were rewritten in place with the TypeScript names; the decisions they record are unchanged.
- Dependencies are composed through InversifyJS inside each Workflow ([Compose dependencies with InversifyJS](compose-dependencies-with-inversifyjs-constructor-injection-and-a-composition-root-per-workflow.md)).
- Known gaps, documented rather than closed: signals, process-group kill, and locking are verified on Linux only, so macOS and Windows behavior is untested; telemetry export is verified against in-memory exporters, not a real collector, and the OpenTelemetry packages are not officially supported on Bun; Docker agent behavior under Bun is unspecified; Bun's FFI is marked experimental, which the lock depends on.
- `archive/` keeps the retired prototypes as a parts source only, never edited or imported.

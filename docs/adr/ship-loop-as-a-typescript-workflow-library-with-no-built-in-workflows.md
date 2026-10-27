# Ship Loop as a TypeScript workflow library with no built-in workflows

> Supersedes [Ship Loop as a workflow library with no built-in workflows](ship-loop-as-a-workflow-library-with-no-built-in-workflows.md). The decision is unchanged; the library and Workflows are now TypeScript on Bun ([Port Loop and Workflows to TypeScript on Bun](port-loop-and-workflows-to-typescript-on-bun-in-one-cutover.md)).

Loop is a library for composing agent workflows: a workflow is an ordinary TypeScript script that wires an agent builder, git services, and a GitHub client, and owns its control flow. The package is named `loop`, exposes one public entry point (`src/loop/index.ts`), and ships no command and no workflows; the user writes every workflow, `dev` included, in the harness-root workflows folder on the public `loop` API, which exports the contracts and the shipped implementations so a workflow never reimplements them. This repository keeps a `dev` workflow only as an example and test subject.

TypeScript `interface`s exist only where Loop's own code calls a replaceable part: `CliRunner`, `GitService`, `DockerService`, `SessionStore`, and the optional `RunObserver`. Clients only a workflow calls (the Spec and Ticket tracker, `PullRequestClient`) ship as concrete helpers inside the Workflow with no contract; a user replaces one by calling their own client from their own workflow, with no dependency on `loop`. Where code checks an optional capability at runtime, such as an observer that offers `span()`, a type guard decides.

## Considered Options

- **A platform-neutral `WorkTracker` interface that the GitHub client implements** — rejected: the workflow is the only caller and is user-owned, so the contract constrains nothing.
- **A single `loop` command whose subcommands are the discovered workflows (`loop dev --option <value>`)** — rejected: a dispatch command needs workflow discovery and a registry contract, while a plain script run through the user's own alias needs neither.
- **Ship built-in workflows (`dev`) inside the `loop` package, resolved before the harness folder** — rejected: workflows are user-owned; repository-specific settings and hooks belong in the user's file, and a built-in `dev` would need an override channel back into it.
- **Expose only a low-level runtime module to user-supplied workflows; they bring their own adapters** — rejected: every workflow needing GitHub or Copilot access reimplements the clients, while the shipped builder, clients, and stores let workflows compose directly.

## Consequences

- Adding the package alone yields no runnable Workflow and no command; a Workflow runs only once the user has an entry point such as `workflows/dev/main.ts` and their own shell alias for it (for example `alias loop-dev='bun workflows/dev/main.ts'`).
- The repository's example `dev` workflow is not part of the compatibility surface.
- The package name in `package.json` and any invocations in skills and docs follow the `loop` name.
- The shipped implementations (builder, git services, agent clients, stores) and the names exported from `src/loop/index.ts` are part of the compatibility surface; breaking them breaks user-supplied workflows. The dependency-injection container is not exported ([Compose dependencies with InversifyJS](compose-dependencies-with-inversifyjs-constructor-injection-and-a-composition-root-per-workflow.md)).
- Importing anything other than the public `loop` entry point from a workflow is a violation, checked directly by the architecture contracts.
- Running a workflow file executes its code; the harness workflows folder is trusted by the act of placing a file there.

See [Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md) for how workflows are composed and run.

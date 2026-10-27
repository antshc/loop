# Loop Library Workflow Architecture
**Type:** Architecture Pattern

## Purpose

Let each autonomous procedure (`dev`, a review loop, a custom flow) be written as plain TypeScript on top of one shared library, instead of a fixed lifecycle. Loop supplies the agent builder and git building blocks; the workflow owns its control flow.

## Concept

Loop is a library of an agent builder, agent CLI adapters, git, and hooks; a **workflow** is an ordinary Bun entry point that wires them and runs its own loop. The example dev Workflow is a self-contained folder with its implementation, prompts, helpers, tests, and fakes together.

- The core of Loop is the **agent builder** and git. `Agent()` returns an `AgentBuilder`; `open(body)` creates a worktree under the harness's `workspace` folder on a branch strategy, runs the callback with clients that run the agent CLI directly on the host in that worktree, and removes the worktree when the callback settles ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
- Loop ships default implementations for git, the Copilot and Codex agent CLIs, and an in-memory session store. The replaceable seams are `CliRunner`, `GitService`, `DockerService`, and `SessionStore`; tests replace them with fakes ([Ship Loop as a TypeScript workflow library](../adr/ship-loop-as-a-typescript-workflow-library-with-no-built-in-workflows.md)). Dev owns its GitHub client, Spec/Ticket tracker, response extraction, commit validation, and publication inside `workflows/dev/`.
- Loop ships no workflows: the user writes every workflow, `dev` included; this repository's `workflows/dev/` is only an example and test subject.
- The example `dev` separates repository routing in `backlog.ts` from Ticket orchestration in `attempt.ts`. A function delegate owns the agent steps and named prompt selection for each configured repository; Ticket orchestration owns history, retries, and commit validation without importing an agent delegate.
- A workflow imports Loop only through its public entry point and is a runnable script: its `main(argv, dependencies)` takes an argument list and returns an exit code, and an `import.meta.main` guard assigns that code to `process.exitCode`. Each workflow parses its own command line with `commander`.
- A workflow composes its dependencies through a composition root: one `Symbol.for` token per seam bound to an InversifyJS container, with `main`'s optional `dependencies` rebinding tokens so tests substitute fakes. Loop's public API does not expose the container ([Compose dependencies with InversifyJS](../adr/compose-dependencies-with-inversifyjs-constructor-injection-and-a-composition-root-per-workflow.md)).
- Loop ships no command: the user runs a workflow script through their own shell alias (for example `alias loop-dev='bun workflows/dev/main.ts'`), with the harness as the current folder ([Port Loop and Workflows to TypeScript on Bun](../adr/port-loop-and-workflows-to-typescript-on-bun-in-one-cutover.md)).
- The workflow's code carries its own settings: harness root, log dir, agent profiles, retry and failure bounds, prompts, and Loop hooks. Dev keeps entry-point settings in `run.ts` and `dependencies.ts`, repository/delegate configuration in `repositories.ts`, and named templates in `prompts/`. Command-line arguments are optional overrides; there is no Loop configuration file or prompt execution framework.
- Inside the library, each layer imports only the layers below it; shared parts never depend on the builder or the Workflows.

## Rules

- MUST ship the builder and default implementations through one public entry point (`src/loop/index.ts`); a workflow MUST import Loop only from it.
- MUST expose an `interface` only where Loop's own code calls a replaceable part (`CliRunner`, `GitService`, `DockerService`, `SessionStore`); dev's concrete GitHub client and tracker live in `platforms.ts`, while Spec/Ticket entities live in `records.ts`, outside the Loop library.
- MUST NOT ship workflows or a command in the `loop` package.
- MUST let a user replace any shipped implementation (git, GitHub tracker, agent builder) with their own through the workflow's injected dependencies.
- MUST run every agent invocation through an `AgentClient` from the builder or an opened `AgentWorkspace`; a workflow MUST NOT call the provider CLI directly.
- MUST create the branch and worktree through `GitOptions` and a branch strategy, by default under the harness's `workspace` folder, and run the Workflow from the harness root so the worktree root is inside the process working directory.
- MUST give each workflow an entry point whose `main(argv, dependencies)` owns its settings and wiring, resolves to the exit code, and runs behind an `import.meta.main` guard; a folder keeps its implementation in local modules and its entry point in `main.ts`.
- MUST accept every external dependency of that entry point as an optional override defaulting to the shipped implementation.
- MUST NOT expose the dependency-injection container through Loop's public API.
- MUST declare a workflow's settings and Loop hooks as code in the workflow module; a workflow MUST run with no command-line arguments and MUST NOT require a configuration file.
- MUST treat command-line arguments as optional overrides of the workflow's coded settings, owned by that workflow.
- MUST NOT couple independent workflow entry points through imports; a user-owned composition script may import a workflow's explicit composition interface, and shared orchestration must not import its agent delegates.
- MUST keep the library layered by import direction (`factory | workflow` > `builder | dryrun` > `agents` > `clis | docker | git` > `run` > `hooks` > `sessions` > `observe`), and dev independent of sibling Workflow modules and helpers; the layering is enforced by the dependency-cruiser contracts in the test run.
- MUST keep process execution (`git`, `gh`, agent CLIs) inside shipped implementations.
- MUST NOT run a workflow file from outside the harness-root workflows folder; running it executes its code.
- MUST place only logic whose business meaning is identical across workflows in the shared library; dev-specific parsing, models, rules, Git reads/publication, and process helpers stay inside `workflows/dev/`. The library returns the agent CLI's raw stdout ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
- SHOULD accept small duplication between workflows over extracting shared code that only looks similar.

## Example

Sketch — not the implementation (derived from the example Workflows):

```text
src/loop/                          # public API: builder, profiles, strategies, hooks (index.ts)
├── Agent(), AgentBuilder, AgentWorkspace   # worktree + host agent runs
├── AgentClient, AgentProfile, copilot, codex, CliRunner
├── GitOptions, BranchStrategy, MergeToHeadStrategy, HeadStrategy, GitService
├── LoopHook (three points), AgentCliHook
└── SessionStore, MemorySessionStore
<harness root>/workflows/          # user-written; imports only the public Loop entry point; main(argv, deps) -> exit code
├── dev/                           # self-contained Workflow; bun workflows/dev/main.ts
│   ├── main.ts                    # entry point
│   ├── run.ts                     # settings and run
│   ├── composition.ts             # composition root (with tokens.ts and dependencies.ts)
│   ├── repositories.ts            # harness and repository-specific delegates
│   ├── backlog.ts                 # Spec routing and publication
│   ├── attempt.ts                 # Ticket delivery, independent of agent steps
│   ├── agent.ts                   # default delegate and named prompt loading
│   ├── responses.ts               # response interpretation
│   ├── journal.ts                 # failure history
│   ├── records.ts                 # work items, identities, outcomes
│   ├── checkout.ts                # Git reads and publication
│   ├── platforms.ts               # GitHub clients and tracker
│   ├── commands.ts                # process execution
│   ├── prompts/                   # named Markdown templates
│   └── tests/                     # local tests and fakes
├── plan_implement.ts              # plan with one model, implement with another, on one worktree
└── review.ts                      # may call its own tracker client
```

```ts
export async function main(argv: readonly string[], dependencies: MyDependencies = {}): Promise<number> {
  const container = compose(dependencies); // binds shipped implementations, then rebinds overrides
  return await container.get<MyWorkflow>(TOKENS.workflow).run(argv);
}

if (import.meta.main) {
  enforceRuntime();
  await runEntrypoint(async () => {
    process.exitCode = await main(process.argv.slice(2));
  });
}
```

Inside the workflow, a run is a callback scope:

```ts
const options = new GitOptions({ strategy: new BranchStrategy(featureBranch, baseBranch), loopHooks: hooks });
await Agent().withGit(options).open(async (worktree) => {
  const { output } = await worktree.agent(profile).run(new AgentRequest(prompt));
  const envelope = extractResponse(output); // workflow-owned parsing
});
```

The user's aliases (`alias loop-dev='bun workflows/dev/main.ts'`) call the script; Loop provides no command.

## Benefits and Trade-offs

**Benefits**

- A workflow is a short readable script; each can choose its own topology (single loop, implement then review, planner then parallel implementers).
- Defaults make a workflow tiny, while any piece (tracker, git, agent client) stays replaceable.
- Workflows are testable with fakes at the process boundary.

**Trade-offs**

- The shipped implementations are part of the compatibility surface for user-supplied workflows.
- Workflows repeat some wiring (accepted, as Sandcastle repeats hooks across templates).
- Running workflow files from the harness workflows folder executes arbitrary code.
- Workflows depend on Bun; Node.js is unsupported.

## Validation

- Workflow tests run against fakes at every process boundary.
- Architecture checks (dependency-cruiser, run by `bun test`) verify the library layering, that workflows import only the public Loop entry point, that platforms import no workflow, and that no workflow imports another workflow; each contract is proven by a seeded violation.
- The example `dev` workflow imports only the public Loop entry point.

## Options Considered

| Option | Outcome |
|--------|---------|
| Vertical slices over domain, contracts, and infrastructure layers (Clean Architecture) | Rejected. The use-case-over-repository shape does not fit agent workflows; layers were mostly empty. |
| Runtime with fixed lifecycle phases; plugins fill slots | Rejected as the base. Removes loop duplication but cannot express planner, implementer, and reviewer flows. |
| Pipeline of replaceable steps | Rejected. Implicit coupling through shared run state and harder tracing. |
| Plugin folders with a `Command` contract, a composition-root `command` module, and runtime-only imports for user workflows | Rejected. The prot2 prototype shows plain scripts over a public library suffice, without the command contract or per-workflow adapters. |
| Built-in workflows shipped in the `loop` package | Rejected ([Ship Loop as a TypeScript workflow library](../adr/ship-loop-as-a-typescript-workflow-library-with-no-built-in-workflows.md)). Repository settings and hooks would need an override channel back into the built-in. |
| **Public library of an agent builder and default implementations; user-written workflows as plain runnable scripts run through the user's own alias** | **Chosen.** Workflows own control flow, as Sandcastle templates do. |

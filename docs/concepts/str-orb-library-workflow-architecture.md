# Orb Library Workflow Architecture
**Type:** Architecture Pattern

## Purpose

Let each autonomous procedure (`dev`, a review loop, a custom flow) be written as plain Python on top of one shared library, instead of a fixed lifecycle. Orb supplies the Capsule, git, and tracker building blocks; the workflow owns its control flow.

## Concept

Orb is a library of contracts and default implementations; a **workflow** is one ordinary Python file that wires them and runs its own loop (the unit Sandcastle calls a template).

- The core of Orb is the **Capsule** and git. A Capsule binds a workspace, runs a prompt template through the agent client either on the host or inside Docker, and is closed when the run ends; git creates the branch and worktree the Capsule works in.
- Orb ships default implementations for git, GitHub, the Copilot CLI agent client, and the stores. Parts Orb's own code calls (Capsule, agent client, stores) sit behind a contract; clients only a workflow calls (`GitClient`, `GitHubClient`) are concrete helpers a user replaces by calling their own client ([ADR 0003](../adr/0003-ship-orb-as-a-workflow-library-with-no-built-in-workflows.md)).
- Orb ships no workflows: the user writes every workflow, `dev` included; this repository's `workflows/dev.py` is only an example and test subject.
- A workflow imports only the public `orb` API and exposes an entry point that takes an argument list and returns an exit code. Dependencies enter that entry point as optional parameters that default to the shipped implementations, so tests substitute fakes ([ADR 0003](../adr/0003-ship-orb-as-a-workflow-library-with-no-built-in-workflows.md)).
- The `orb` command is an alias: `orb <name>` runs `workflows/<name>.py` under the git top-level of cwd ([ADR 0002](../adr/0002-ship-orb-as-the-orb-package-with-an-orb-command.md)).
- The workflow file is the workflow's DSL and carries its own settings as code: harness root, log dir, the agent, retry and attempt bounds, prompt template, dry run, Capsule choice and its Docker settings, and the lifecycle hooks it passes to worktree creation. Command-line arguments are optional overrides the workflow chooses to expose; there is no Orb configuration file.
- Inside the library, contracts and policy shared by every workflow (for example the attempt cap) never depend on the implementations.

## Rules

- MUST ship contracts and default implementations through one public top-level `orb` API; a workflow MUST import only from it, and workflow tests MAY also import the shipped test doubles from `orb.testing`.
- MUST define a contract as an `abc.ABC` only where Orb's own code calls a replaceable part (Capsule, agent client, stores), and make every implementation of it inherit it.
- MUST ship clients only a workflow calls (`GitClient`, `GitHubClient`) as concrete helpers with no contract.
- MUST NOT ship workflows in the `orb` package.
- MUST let a user replace any shipped implementation (git, GitHub tracker, agent client, stores) with their own through the workflow's injected dependencies.
- MUST run every agent invocation through a Capsule; a workflow MUST NOT call the agent client or provider CLI directly.
- MUST create the branch and worktree through the git client and give that worktree to the Capsule.
- MUST define each platform-neutral model next to the contract that returns it; there is no separate domain layer.
- MUST write each workflow as one Python file that owns its settings, control flow, and wiring and exposes one entry point returning the exit code.
- MUST accept every external dependency of that entry point as an optional parameter defaulting to the shipped implementation.
- MUST declare a workflow's settings and lifecycle hooks as code in the workflow module; a workflow MUST run with no command-line arguments and MUST NOT require a configuration file.
- MUST treat command-line arguments as optional overrides of the workflow's coded settings, owned by that workflow.
- MUST keep the CLI limited to resolving `<name>` to a workflow file, running it, and mapping the result to the process exit code.
- MUST resolve `<name>` only to `workflows/<name>.py` under the git top-level of cwd, without a central registry, and fail on an unknown name.
- MUST NOT let a workflow import another workflow.
- MUST keep contracts and shared policy free of imports from shipped implementations.
- MUST keep process execution (`git`, `gh`, `copilot`, `docker`) inside shipped implementations.
- MUST NOT run a workflow file from outside the harness-root workflows folder; running it executes its code.
- MUST place only logic whose business meaning is identical across workflows in the shared library policy; workflow-specific parsing, models, and rules (e.g. `dev`'s report parser) stay in the workflow file.
- SHOULD accept small duplication between workflows over extracting shared code that only looks similar.

## Example

Sketch — not the implementation (derived from the prot2 prototype):

```text
orb/                              # public API: contracts + default implementations
├── Capsule, NoCapsule, DockerCapsule     # contracts + implementations
├── AgentClient, CopilotClient
├── ExecutionStore, SessionStore, file stores
├── GitClient, GitHubClient               # concrete helpers, no contract
└── policy (attempt cap)
<harness root>/workflows/         # user-written; imports only `orb`; main(argv) -> int
├── dev.py
└── review.py                     # may call its own tracker client
```

```python
def main(argv=None, *, git=None, github=None, store=None, capsule_factory=None) -> int:
    git = git or GitClient(repo)
    github = github or GitHubClient.for_repo(repo)
    ...
    with capsule_factory(worktree, agent_factory) as capsule:
        capsule.run(template, prompt_args, options)
```

`orb dev` and `orb review` call the harness files' entry points.

## Benefits and Trade-offs

**Benefits**

- A workflow is a short readable script; each can choose its own topology (single loop, implement then review, planner then parallel implementers).
- Defaults make a workflow tiny, while any piece (tracker, git, agent client) stays replaceable.
- Workflows are testable with fakes at the process boundary.

**Trade-offs**

- The shipped implementations are part of the compatibility surface for user-supplied workflows.
- Workflows repeat some wiring (accepted, as Sandcastle repeats hooks across templates).
- Running workflow files from the harness workflows folder executes arbitrary code.

## Validation

- Workflow tests run against fakes at every process boundary.
- Architecture checks (import-linter) verify that the shared contracts and policy import no implementation and that no workflow imports another workflow.
- The example `dev` workflow imports only the public `orb` API.
- Dispatching an unknown workflow name fails.

## Options Considered

| Option | Outcome |
|--------|---------|
| Vertical slices over domain, contracts, and infrastructure layers (Clean Architecture) | Rejected. The use-case-over-repository shape does not fit agent workflows; layers were mostly empty. |
| Runtime with fixed lifecycle phases; plugins fill slots | Rejected as the base. Removes loop duplication but cannot express planner, implementer, and reviewer flows. |
| Pipeline of replaceable steps | Rejected. Implicit coupling through shared run state and harder tracing. |
| Plugin folders with a `Command` contract, a composition-root `command.py`, and runtime-only imports for user workflows | Rejected. The prot2 prototype shows plain scripts over a public library suffice, without the command contract or per-workflow adapters. |
| Built-in workflows shipped in the `orb` package | Rejected ([ADR 0003](../adr/0003-ship-orb-as-a-workflow-library-with-no-built-in-workflows.md)). Repository settings and hooks would need an override channel back into the built-in. |
| **Public library of contracts and default implementations; user-written workflows as plain modules dispatched by `orb <name>`** | **Chosen.** Workflows own control flow, as Sandcastle templates do. |

# Loop Library Workflow Architecture
**Type:** Architecture Pattern

## Purpose

Let each autonomous procedure (`dev`, a review loop, a custom flow) be written as plain Python on top of one shared library, instead of a fixed lifecycle. Loop supplies the worktree runner, git, and tracker building blocks; the workflow owns its control flow.

## Concept

Loop is a library of contracts and default implementations; a **workflow** is one ordinary Python file that wires them and runs its own loop (the unit Sandcastle calls a template).

- The core of Loop is the **worktree runner** and git. A worktree runner owns one worktree under the harness's `workspace` folder and one agent client, runs the agent directly on the host from the harness root, and removes the worktree when disposed; git creates the branch and worktree.
- Loop ships default implementations for git, the Copilot CLI agent client, and the stores. Parts Loop's own code calls (agent client, stores) sit behind a contract; the git services (`BranchService`, `WorktreeService`, `CommitService`) are concrete deep modules with no contract, replaced in tests by fakes that subclass them ([ADR 0003](../adr/0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md), [ADR 0010](../adr/0010-split-git-access-into-concrete-branch-worktree-and-commit-services.md)). The GitHub client and Spec/Ticket tracker are workflow-side code in `workflows/platforms/work_tracking`.
- Loop ships no workflows: the user writes every workflow, `dev` included; this repository's `workflows/dev.py` is only an example and test subject.
- A workflow imports only the public `loop` API and is a runnable script: its entry point takes an argument list and returns an exit code, and a `__main__` guard passes that code to the process exit. Dependencies enter that entry point as optional parameters that default to the shipped implementations, so tests substitute fakes ([ADR 0003](../adr/0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).
- Loop ships no command: the user runs a workflow script through their own shell alias (for example `alias loop-dev='python workflows/dev.py'`), with the harness as the current folder ([ADR 0003](../adr/0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md); [ADR 0002](../adr/0002-ship-loop-as-the-loop-package-with-an-loop-command.md) is superseded).
- The workflow file is the workflow's DSL and carries its own settings as code: harness root, log dir, the agent, retry and failure bounds, prompt template, and the lifecycle hooks it passes to worktree creation. Command-line arguments are optional overrides the workflow chooses to expose; there is no Loop configuration file.
- Inside the library, contracts and policy shared by every workflow (for example the attempt cap) never depend on the implementations.

## Rules

- MUST ship contracts and default implementations through one public top-level `loop` API; a workflow MUST import only from it, and workflow tests MAY also import the shipped test doubles from `loop.testing`.
- MUST define a contract as an `abc.ABC` only where Loop's own code calls a replaceable part (agent client, stores), and make every implementation of it inherit it.
- MUST ship the git services (`BranchService`, `WorktreeService`, `CommitService`) as concrete classes with no contract, taking and returning `Branch`, `Worktree`, and `Commit` entities so callers state intent and never choose refs or git commands; the GitHub client in `workflows/platforms/work_tracking` stays a pure `gh` wrapper returning raw issue nodes, while the Spec/Ticket entities and actionable-Ticket selection live in that package's tracker, not in the loop library or the workflow file.
- MUST NOT ship workflows or a command in the `loop` package.
- MUST let a user replace any shipped implementation (git, GitHub tracker, agent client, stores) with their own through the workflow's injected dependencies.
- MUST run every agent invocation through a worktree runner; a workflow MUST NOT call the agent client or provider CLI directly.
- MUST create the branch and worktree through the git services, by default under the harness's `workspace` folder, and run agents from the harness root so it contains the worktree.
- MUST define each platform-neutral model next to the contract that returns it; there is no separate domain layer.
- MUST write each workflow as one Python file that owns its settings, control flow, and wiring and exposes one entry point returning the exit code, run by a `__main__` guard.
- MUST accept every external dependency of that entry point as an optional parameter defaulting to the shipped implementation.
- MUST declare a workflow's settings and lifecycle hooks as code in the workflow module; a workflow MUST run with no command-line arguments and MUST NOT require a configuration file.
- MUST treat command-line arguments as optional overrides of the workflow's coded settings, owned by that workflow.
- MUST NOT let a workflow import another workflow.
- MUST keep contracts and shared policy free of imports from shipped implementations.
- MUST keep process execution (`git`, `gh`, `copilot`) inside shipped implementations.
- MUST NOT run a workflow file from outside the harness-root workflows folder; running it executes its code.
- MUST place only logic whose business meaning is identical across workflows in the shared library policy; workflow-specific parsing, models, and rules (e.g. `dev`'s `result` model and Git validation) stay in the workflow file; extracting the agent's response envelope from provider output is library work owned by the agent kind's output parser ([ADR 0007](../adr/0007-stream-agent-output-live-and-parse-it-after-exit-with-a-per-agent-kind-output-parser.md)).
- SHOULD accept small duplication between workflows over extracting shared code that only looks similar.

## Example

Sketch — not the implementation (derived from the prot2 prototype):

```text
loop/                              # public API: contracts + default implementations
├── AgentRunnerProvider, AgentRunner, WorktreeLifecycle  # worktree + host agent runs
├── AgentClient, CopilotClient
├── ExecutionStore, SessionStore, file stores
├── BranchService, WorktreeService, CommitService  # concrete, no contract
└── policy (attempt cap)
<harness root>/workflows/         # user-written; imports only `loop`; main(argv) -> int
├── platforms/work_tracking/      # GitHubClient (gh wrapper), Spec/Ticket entities, TicketsTracker
├── dev.py                        # runs via the user's alias
└── review.py                     # may call its own tracker client
```

```python
def main(argv=None, *, branches=None, commits=None, github_factory=None, store=None, executor=None) -> int:
    branches = branches or BranchService()
    github_factory = github_factory or (lambda checkout: GitHubClient.for_repo(checkout)[0])
    ...
    provider = AgentRunnerProvider(git, harness_root, agent, executor=executor)
    with provider.create(checkout=..., worktree_root=..., base=...) as runner:
        runner.run(template, prompt_args, options)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

The user's aliases (`alias loop-dev='python workflows/dev.py'`) call the script; Loop provides no command.

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
- The example `dev` workflow imports only the public `loop` API.

## Options Considered

| Option | Outcome |
|--------|---------|
| Vertical slices over domain, contracts, and infrastructure layers (Clean Architecture) | Rejected. The use-case-over-repository shape does not fit agent workflows; layers were mostly empty. |
| Runtime with fixed lifecycle phases; plugins fill slots | Rejected as the base. Removes loop duplication but cannot express planner, implementer, and reviewer flows. |
| Pipeline of replaceable steps | Rejected. Implicit coupling through shared run state and harder tracing. |
| Plugin folders with a `Command` contract, a composition-root `command.py`, and runtime-only imports for user workflows | Rejected. The prot2 prototype shows plain scripts over a public library suffice, without the command contract or per-workflow adapters. |
| Built-in workflows shipped in the `loop` package | Rejected ([ADR 0003](../adr/0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)). Repository settings and hooks would need an override channel back into the built-in. |
| **Public library of contracts and default implementations; user-written workflows as plain runnable scripts run through the user's own alias** | **Chosen.** Workflows own control flow, as Sandcastle templates do. |

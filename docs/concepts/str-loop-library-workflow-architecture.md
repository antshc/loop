# Loop Library Workflow Architecture
**Type:** Architecture Pattern

## Purpose

Let each autonomous procedure (`dev`, a review loop, a custom flow) be written as plain Python on top of one shared library, instead of a fixed lifecycle. Loop supplies the agent builder and git building blocks; the workflow owns its control flow.

## Concept

Loop is a library of an agent builder, agent CLI adapters, git, and hooks; a **workflow** is one ordinary Python file that wires them and runs its own loop (the unit Sandcastle calls a template).

- The core of Loop is the **agent builder** and git. `Agent()` returns an `AgentBuilder`; `open()` creates a worktree under the harness's `workspace` folder on a branch strategy and yields clients that run the agent CLI directly on the host in that worktree, and removes the worktree on exit ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
- Loop ships default implementations for git, the Copilot and Codex agent CLIs, and an in-memory session store. The replaceable seams are `CliRunner`, `GitService`, `DockerService`, and `SessionStore`; tests replace them with fakes ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)). The GitHub client, Spec/Ticket tracker, response extraction, commit validation, and publication are workflow-side code in `workflows/platforms/` and the Workflow.
- Loop ships no workflows: the user writes every workflow, `dev` included; this repository's `workflows/dev.py` is only an example and test subject.
- A workflow imports only the public `loop` API and is a runnable script: its entry point takes an argument list and returns an exit code, and a `__main__` guard passes that code to the process exit. Dependencies enter that entry point as optional parameters that default to the shipped implementations, so tests substitute fakes ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).
- Loop ships no command: the user runs a workflow script through their own shell alias (for example `alias loop-dev='python -m workflows.dev'`), with the harness as the current folder ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).
- The workflow file is the workflow's DSL and carries its own settings as code: harness root, log dir, the agent profile, retry and failure bounds, prompt template, and the Loop hooks it passes in `GitOptions`. Command-line arguments are optional overrides the workflow chooses to expose; there is no Loop configuration file.
- Inside the library, each layer imports only the layers below it; shared parts never depend on the builder or the Workflows.

## Rules

- MUST ship the builder and default implementations through one public top-level `loop` API; a workflow MUST import only from it.
- MUST expose a Protocol only where Loop's own code calls a replaceable part (`CliRunner`, `GitService`, `DockerService`, `SessionStore`); the GitHub client in `workflows/platforms/work_tracking` stays a pure `gh` wrapper returning raw issue nodes, while the Spec/Ticket entities and actionable-Ticket selection live in that package's tracker, not in the loop library or the workflow file.
- MUST NOT ship workflows or a command in the `loop` package.
- MUST let a user replace any shipped implementation (git, GitHub tracker, agent builder) with their own through the workflow's injected dependencies.
- MUST run every agent invocation through an `AgentClient` from the builder or an opened `Worktree`; a workflow MUST NOT call the provider CLI directly.
- MUST create the branch and worktree through `GitOptions` and a branch strategy, by default under the harness's `workspace` folder, and run the Workflow from the harness root so the worktree root is inside the process working directory.
- MUST write each workflow as one Python file that owns its settings, control flow, and wiring and exposes one entry point returning the exit code, run by a `__main__` guard.
- MUST accept every external dependency of that entry point as an optional parameter defaulting to the shipped implementation.
- MUST declare a workflow's settings and Loop hooks as code in the workflow module; a workflow MUST run with no command-line arguments and MUST NOT require a configuration file.
- MUST treat command-line arguments as optional overrides of the workflow's coded settings, owned by that workflow.
- MUST NOT let a workflow import another workflow.
- MUST keep the library layered by import direction (`factory` > `builder` > `agents` > `clis | docker | git` > `run` > `hooks` > `sessions`), and `workflows/platforms` free of Workflow imports.
- MUST keep process execution (`git`, `gh`, agent CLIs) inside shipped implementations.
- MUST NOT run a workflow file from outside the harness-root workflows folder; running it executes its code.
- MUST place only logic whose business meaning is identical across workflows in the shared library; workflow-specific parsing, models, and rules (e.g. `dev`'s `result` model, response envelope, and Git validation) stay in the Workflow or `workflows/platforms`; the library returns the agent CLI's raw stdout ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
- SHOULD accept small duplication between workflows over extracting shared code that only looks similar.

## Example

Sketch — not the implementation (derived from the example Workflows):

```text
loop/                              # public API: builder, profiles, strategies, hooks
├── Agent(), AgentBuilder, Worktree      # worktree + host agent runs
├── AgentClient, AgentProfile, copilot, codex, CliRunner
├── GitOptions, BranchStrategy, MergeToHeadStrategy, HeadStrategy, GitService
├── LoopHook (three points), AgentCliHook
└── SessionStore, MemorySessionStore
<harness root>/workflows/         # user-written; imports only `loop`; main(argv) -> int
├── platforms/work_tracking/      # GitHubClient (gh wrapper), Spec/Ticket entities, TicketsTracker
├── dev/                          # runs via the user's alias: python -m workflows.dev
├── plan_implement.py             # plan with one model, implement with another, on one worktree
└── review.py                     # may call its own tracker client
```

```python
def main(argv=None, *, git=None, github_factory=None, new_agent=Agent, store=None) -> int:
    git = git or WorkflowGit()
    ...
    options = GitOptions(strategy=BranchStrategy(feature_branch, base_branch), loop_hooks=hooks)
    with new_agent().with_git(options).open() as worktree:
        result = worktree.agent(profile).run(AgentRequest(prompt))
        envelope = extract_response(result.output)   # workflow-owned parsing

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

The user's aliases (`alias loop-dev='python -m workflows.dev'`) call the script; Loop provides no command.

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
- Architecture checks (import-linter) verify the library layering, that workflows import only the public `loop` API, that platforms import no workflow, and that no workflow imports another workflow.
- The example `dev` workflow imports only the public `loop` API.

## Options Considered

| Option | Outcome |
|--------|---------|
| Vertical slices over domain, contracts, and infrastructure layers (Clean Architecture) | Rejected. The use-case-over-repository shape does not fit agent workflows; layers were mostly empty. |
| Runtime with fixed lifecycle phases; plugins fill slots | Rejected as the base. Removes loop duplication but cannot express planner, implementer, and reviewer flows. |
| Pipeline of replaceable steps | Rejected. Implicit coupling through shared run state and harder tracing. |
| Plugin folders with a `Command` contract, a composition-root `command.py`, and runtime-only imports for user workflows | Rejected. The prot2 prototype shows plain scripts over a public library suffice, without the command contract or per-workflow adapters. |
| Built-in workflows shipped in the `loop` package | Rejected ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)). Repository settings and hooks would need an override channel back into the built-in. |
| **Public library of an agent builder and default implementations; user-written workflows as plain runnable scripts run through the user's own alias** | **Chosen.** Workflows own control flow, as Sandcastle templates do. |

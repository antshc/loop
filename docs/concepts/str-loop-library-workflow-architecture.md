# Loop Library Workflow Architecture
**Type:** Architecture Pattern

## Purpose

Let each autonomous procedure (`dev`, a review loop, a custom flow) be written as plain Python on top of one shared library, instead of a fixed lifecycle. Loop supplies the agent builder and git building blocks; the workflow owns its control flow.

## Concept

Loop is a library of an agent builder, agent CLI adapters, git, and hooks; a **workflow** is an ordinary Python entry point that wires them and runs its own loop. The example dev Workflow is a self-contained package with its implementation, prompts, helpers, tests, and fakes together.

- The core of Loop is the **agent builder** and git. `Agent()` returns an `AgentBuilder`; `open()` creates a worktree under the harness's `workspace` folder on a branch strategy and yields clients that run the agent CLI directly on the host in that worktree, and removes the worktree on exit ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
- Loop ships default implementations for git, the Copilot and Codex agent CLIs, and an in-memory session store. The replaceable seams are `CliRunner`, `GitService`, `DockerService`, and `SessionStore`; tests replace them with fakes ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)). Dev owns its GitHub client, Spec/Ticket tracker, response extraction, commit validation, and publication inside `workflows/dev/`.
- Loop ships no workflows: the user writes every workflow, `dev` included; this repository's `workflows/dev/` is only an example and test subject.
- The example `dev` separates repository routing in `backlog.py` from Ticket orchestration in `attempt.py`. A function delegate owns the agent steps and named prompt selection for each configured repository; Ticket orchestration owns history, retries, and commit validation without importing an agent delegate.
- A workflow imports only the public `loop` API and is a runnable script: its entry point takes an argument list and returns an exit code, and a `__main__` guard passes that code to the process exit. Dependencies enter that entry point as optional parameters that default to the shipped implementations, so tests substitute fakes ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).
- Loop ships no command: the user runs a workflow script through their own shell alias (for example `alias loop-dev='python -m workflows.dev'`), with the harness as the current folder ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).
- The workflow's Python code carries its own settings: harness root, log dir, agent profiles, retry and failure bounds, prompts, and Loop hooks. Dev keeps entry-point settings in `run.py`, repository/delegate configuration in `repositories.py`, and named templates in `prompts/`. Command-line arguments are optional overrides; there is no Loop configuration file or prompt execution framework.
- Inside the library, each layer imports only the layers below it; shared parts never depend on the builder or the Workflows.

## Rules

- MUST ship the builder and default implementations through one public top-level `loop` API; a workflow MUST import only from it.
- MUST expose a Protocol only where Loop's own code calls a replaceable part (`CliRunner`, `GitService`, `DockerService`, `SessionStore`); dev's concrete GitHub client and tracker live in `platforms.py`, while Spec/Ticket entities live in `records.py`, outside the Loop library.
- MUST NOT ship workflows or a command in the `loop` package.
- MUST let a user replace any shipped implementation (git, GitHub tracker, agent builder) with their own through the workflow's injected dependencies.
- MUST run every agent invocation through an `AgentClient` from the builder or an opened `Worktree`; a workflow MUST NOT call the provider CLI directly.
- MUST create the branch and worktree through `GitOptions` and a branch strategy, by default under the harness's `workspace` folder, and run the Workflow from the harness root so the worktree root is inside the process working directory.
- MUST give each workflow a Python entry point that owns its settings and wiring, returns the exit code, and runs through a `__main__` guard; a package may use `__main__.py` and keep its implementation in local modules.
- MUST accept every external dependency of that entry point as an optional parameter defaulting to the shipped implementation.
- MUST declare a workflow's settings and Loop hooks as code in the workflow module; a workflow MUST run with no command-line arguments and MUST NOT require a configuration file.
- MUST treat command-line arguments as optional overrides of the workflow's coded settings, owned by that workflow.
- MUST NOT couple independent workflow entry points through imports; a user-owned composition script may import a workflow's explicit composition interface, and shared orchestration must not import its agent delegates.
- MUST keep the library layered by import direction (`factory` > `builder` > `agents` > `clis | docker | git` > `run` > `hooks` > `sessions`), and dev independent of sibling Workflow modules and helpers.
- MUST keep process execution (`git`, `gh`, agent CLIs) inside shipped implementations.
- MUST NOT run a workflow file from outside the harness-root workflows folder; running it executes its code.
- MUST place only logic whose business meaning is identical across workflows in the shared library; dev-specific parsing, models, rules, Git reads/publication, and process helpers stay inside `workflows/dev/`. The library returns the agent CLI's raw stdout ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
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
<harness root>/workflows/         # user-written; imports only public `loop`; main(argv) -> int
├── dev/                          # self-contained Workflow; python -m workflows.dev
│   ├── run.py                    # settings and entry point
│   ├── repositories.py           # harness and repository-specific delegates
│   ├── backlog.py                # Spec routing and publication
│   ├── attempt.py                # Ticket delivery, independent of agent steps
│   ├── agent.py                  # default delegate and named prompt loading
│   ├── reply.py                  # response interpretation
│   ├── journal.py                # failure history
│   ├── records.py                # work items, identities, outcomes
│   ├── checkout.py               # Git reads and publication
│   ├── platforms.py              # GitHub clients and tracker
│   ├── commands.py               # subprocess execution
│   ├── prompts/                  # named Markdown templates
│   └── tests/                    # local tests and fakes
├── plan_implement.py             # plan with one model, implement with another, on one worktree
└── review.py                     # may call its own tracker client
```

```python
def main(argv=None, *, git=None, github_factory=None, new_agent=Agent, store=None) -> int:
    git = git or GitService()
    ...
    options = GitOptions(strategy=BranchStrategy(feature_branch, base_branch), loop_hooks=hooks)
    with new_agent().with_git(options).open() as worktree:
        output = worktree.agent(profile).run(AgentRequest(prompt)).output
        envelope = extract_response(output)   # workflow-owned parsing

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

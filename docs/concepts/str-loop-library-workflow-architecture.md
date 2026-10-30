# Loop Library Workflow Architecture
**Type:** Architecture Pattern

## Purpose

Let each autonomous procedure (`implement_spec`, a review loop, a custom flow) be written as plain Python on top of one shared library, instead of a fixed lifecycle. Loop supplies the agent builder and git building blocks; the workflow owns its control flow.

## Concept

Loop is a library of an agent builder, agent CLI adapters, git, and hooks; a **workflow** is an ordinary Python entry point that wires them and runs its own loop. The `implement_spec` Workflow is one declarative definition over the factory's Stage library, run by the factory runtime; `pr_babysit` is still a vertical slice (one package per feature plus `model`, `agents`, and a `composition` root); both sit over the infrastructure in `software_factory/shared/` ([Structure workflows as vertical slices](../adr/structure-workflows-as-vertical-slices-over-a-shared-package-wired-by-dishka.md), for the slice).

- The core of Loop is the **agent builder** and git. `Agent()` returns an `AgentBuilder`; `open()` creates a worktree under the harness's `workspace` folder on a branch strategy and yields clients that run the agent CLI directly on the host in that worktree, and removes the worktree on exit ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
- Loop ships default implementations for git, the Copilot and Codex agent CLIs, and an in-memory session store. The replaceable seams are `CliRunner`, `GitService`, `DockerService`, and `SessionStore`; tests replace them with fakes ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)). `software_factory/shared/` owns the Workflow-side Git, GitHub (issues, pull requests, and the Spec and Ticket model), subprocess, JSON-state, envelope, and telemetry helpers; implement_spec owns its delivery policy in `software_factory/workflows/implement_spec/policy.py`, and the Stage library in `software_factory/stages/` owns response decoding, commit validation, and publication ([Share the Spec and Ticket model](../adr/share-the-spec-and-ticket-model-in-workflows-shared-while-dev-keeps-its-delivery-policy.md)).
- Loop ships no workflows: the user writes every workflow, `implement_spec` included; this repository's `software_factory/workflows/implement_spec/` is only an example and test subject.
- The example `implement_spec` is a definition (`__init__.py`) that places Stages and Flow fragments from the factory's Stage library; the Stages are independent of each other and of the definition, and per-repository variability is data on the routed `RepositoryTarget`, not a delegate.
- A workflow imports only the public `loop` API and is a runnable script: its entry point takes an argument list and returns an exit code, and a `__main__` guard passes that code to the process exit. `implement_spec` builds its services by hand (no container) and `main(argv, *, services=None)` accepts a `Services` bundle so tests substitute fakes; `pr_babysit` still builds a Dishka container in its `composition/` root and accepts override providers ([Structure workflows as vertical slices](../adr/structure-workflows-as-vertical-slices-over-a-shared-package-wired-by-dishka.md)).
- Loop ships no command: the user runs a workflow script through their own shell alias (for example `alias loop-implement-spec='python -m software_factory.workflows.implement_spec'`), with the harness as the current folder ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).
- The workflow's Python code carries its own settings: harness root, log dir, agent profiles, retry and failure bounds, prompts, and Loop hooks. `implement_spec` keeps entry-point settings in `__main__.py` and `settings.py`, its policy values in `policy.py`, and its role agents' prompt templates in `software_factory/agents/roles/<role>/prompt.md`. Command-line arguments are optional overrides; there is no Loop configuration file or prompt execution framework.
- Inside the library, each layer imports only the layers below it; shared parts never depend on the builder or the Workflows.

## Rules

- MUST ship the builder and default implementations through one public top-level `loop` API; a workflow MUST import only from it.
- MUST expose a Protocol only where Loop's own code calls a replaceable part (`CliRunner`, `GitService`, `DockerService`, `SessionStore`); the Workflow-side GitHub clients, including the Spec and Ticket model, live in `software_factory/shared/github/`, outside the Loop library.
- MUST NOT ship workflows or a command in the `loop` package.
- MUST let a user replace any shipped implementation (git, GitHub tracker, agent builder) with their own through the workflow's injected dependencies.
- MUST run every agent invocation through an `AgentClient` from the builder or an opened `Worktree`; a workflow MUST NOT call the provider CLI directly.
- MUST create the branch and worktree through `GitOptions` and a branch strategy, by default under the harness's `workspace` folder, and run the Workflow from the harness root so the worktree root is inside the process working directory.
- MUST give each workflow a Python entry point that owns its settings and wiring, returns the exit code, and runs through a `__main__` guard; a package may use `__main__.py` and keep its implementation in local modules.
- MUST let callers replace every external dependency by passing `Services` (`implement_spec`) or a Dishka override provider (`pr_babysit`) to the entry point; only the `pr_babysit` composition root and `software_factory/shared/providers.py` may import `dishka`.
- MUST declare a workflow's settings and Loop hooks as code in the workflow module; a workflow MUST run with no command-line arguments and MUST NOT require a configuration file.
- MUST treat command-line arguments as optional overrides of the workflow's coded settings, owned by that workflow.
- MUST keep each Workflow package's tests under `software_factory/tests/` (`workflows/<name>/`, `legacy_slices/<slice>/` and `shared/`), mirroring the package's source folders; shared GitHub fakes live in `software_factory/tests/shared/fakes/` and workflow harnesses reuse or extend them.
- MUST NOT couple independent workflows through imports; both may import `software_factory/shared/`, which imports no workflow. A user-owned composition script may import a workflow's explicit composition interface. A Workflow definition MUST NOT import another, `runtime._engine` or `agents._kit`, and its policy module MUST import no process, Git or GitHub client.
- MUST keep the library layered by import direction (`factory` > `builder` > `agents` > `clis | docker | git` > `run` > `hooks` > `sessions`), and each workflow independent of sibling Workflows.
- MUST keep process execution (`git`, `gh`, agent CLIs) inside shipped implementations.
- MUST NOT run a workflow file from outside the harness-root workflows folder; running it executes its code.
- MUST place only logic whose business meaning is identical across workflows in `software_factory/shared/` (or, for every workflow, the `loop` library); workflow-specific models, rules, prompts, and orchestration stay inside the slice, except the Spec and Ticket model, which lives in `software_factory/shared/github/` while implement_spec keeps its delivery policy. The library returns the agent CLI's raw stdout ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
- SHOULD accept small duplication between workflows over extracting shared code that only looks similar; group code by feature, not by technical layer.

## Example

Sketch — not the implementation (derived from the example Workflows):

```text
loop/                              # public API: builder, profiles, strategies, hooks
├── Agent(), AgentBuilder, Worktree      # worktree + host agent runs
├── AgentClient, AgentProfile, copilot, codex, CliRunner
├── GitOptions, BranchStrategy, MergeToHeadStrategy, HeadStrategy, GitService
├── LoopHook (three points), AgentCliHook
└── SessionStore, MemorySessionStore
<harness root>/software_factory/  # not in the loop distribution; imports only public `loop`
├── shared/                       # infrastructure identical across workflows; imports no workflow
│   ├── git.py, process.py, state.py, telemetry.py
│   ├── github/                   # gh client, repository, issues (Spec, Ticket), pull requests
│   ├── agents/                   # agent launcher, envelopes, prompt files
│   └── providers.py              # SharedProvider and bind() for overrides
├── workflows/                    # Workflow definitions; main(argv, *, services=None) -> int
│   └── implement_spec/           # python -m software_factory.workflows.implement_spec
│       ├── __init__.py           # the definition over the Stage library
│       ├── policy.py             # hitl label, hand-off texts, subjects, work item, release rule
│       ├── intake.py, settings.py
│       ├── __main__.py           # entry point, services, console and OpenTelemetry spans
│       └── dry.py                # --fake/--dry seams
├── legacy_slices/                # temporary; main(argv, *, providers=()) -> int
│   └── pr_babysit/               # vertical slice: discover, monitor, watch, address, maintain_ci, publish
└── tests/                        # workflows/implement_spec/, legacy_slices/pr_babysit/ and shared/ (fakes/) mirror the source folders
<harness root>/workflows/         # standalone user-written examples; import only public `loop`
├── plan_implement.py             # plan with one model, implement with another, on one worktree
└── review.py                     # may call its own tracker client
```

```python
class Planner:
    def __init__(self, new_agent: AgentLauncher, git: GitClient, tracker: Tracker, phases: Phases) -> None: ...

class ImplementSpecProvider(Provider):
    planner = provide(Planner, scope=Scope.REQUEST)   # one per Workflow run

def main(argv=None, *, providers: Sequence[Provider] = ()) -> int:
    settings = parse(argv)
    container = make_container(SharedProvider(), ImplementSpecProvider(), *providers, context={ImplementSpecSettings: settings})
    with container(context={Phases: phases}) as run:
        return run.get(SpecRouter).run()

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

The user's aliases (`alias loop-implement-spec='python -m software_factory.workflows.implement_spec'`) call the script; Loop provides no command.

## Benefits and Trade-offs

**Benefits**

- A workflow is a short readable script; each can choose its own topology (single loop, implement then review, planner then parallel implementers).
- Defaults make a workflow tiny, while any piece (tracker, git, agent client) stays replaceable by one override provider.
- Workflows are testable with fakes at the process boundary.

**Trade-offs**

- The shipped implementations are part of the compatibility surface for user-supplied workflows.
- Workflows repeat some wiring (accepted, as Sandcastle repeats hooks across templates), and running `implement_spec` or `pr_babysit` needs the `workflows` extra for Dishka.
- Running workflow files from the harness workflows folder executes arbitrary code.

## Validation

- Workflow tests run against fakes at every process boundary.
- Architecture checks (import-linter) verify the library layering, that workflows import only the public `loop` API, that `software_factory/shared` imports no workflow, that the pure modules of the shared GitHub `issues` package import no `gh` process code, that only composition roots import `dishka`, that features never import their composition root, and that no workflow imports another workflow.
- The example workflows import only the public `loop` API and `software_factory/shared`.

## Options Considered

| Option | Outcome |
|--------|---------|
| Horizontal domain, contracts, and infrastructure layers (Clean Architecture) | Rejected. The use-case-over-repository shape does not fit agent workflows; layers were mostly empty. |
| Feature-sliced workflows over `software_factory/shared/` with a Dishka composition root | Chosen for workflow internals ([Structure workflows as vertical slices](../adr/structure-workflows-as-vertical-slices-over-a-shared-package-wired-by-dishka.md)). |
| Runtime with fixed lifecycle phases; plugins fill slots | Rejected as the base. Removes loop duplication but cannot express planner, implementer, and reviewer flows. |
| Pipeline of replaceable steps | Rejected. Implicit coupling through shared run state and harder tracing. |
| Plugin folders with a `Command` contract, a composition-root `command.py`, and runtime-only imports for user workflows | Rejected. The prot2 prototype shows plain scripts over a public library suffice, without the command contract or per-workflow adapters. |
| Built-in workflows shipped in the `loop` package | Rejected ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)). Repository settings and hooks would need an override channel back into the built-in. |
| **Public library of an agent builder and default implementations; user-written workflows as plain runnable scripts run through the user's own alias** | **Chosen.** Workflows own control flow, as Sandcastle templates do. |

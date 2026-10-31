# Loop Library Workflow Architecture
**Type:** Architecture Pattern

## Purpose

Let each autonomous procedure (`implement_spec`, a review loop, a custom flow) be written as a short declarative Workflow on top of one shared library, instead of a fixed lifecycle. Loop supplies the agent builder, the Workflow runtime, the typed agent contract and the reusable Git, process and GitHub helpers; the Workflow owns its Stages, policy and settings.

## Concept

Loop is a library of an agent builder, agent CLI adapters, git and hooks, the declarative Workflow runtime ([Factory Workflow Runtime](str-factory-workflow-runtime.md)), the typed agent contract, and shared Workflow infrastructure; a **workflow** is a user-owned Python entry point that builds a definition, passes its `Services` and runs it. The `implement_spec`, `pr_babysit` and `address_once` Workflows are definitions over the factory's Stage library in `workflows/software_factory/`; the `plan_implement` example in `workflows/loop/` uses its own Stages.

- The core of Loop is the **agent builder** and git. `Agent()` returns an `AgentBuilder`; `open()` creates a worktree under the harness's `workspace` folder on a branch strategy and yields clients that run the agent CLI directly on the host in that worktree, and removes the worktree on exit ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
- The **runtime** (`loop.runtime`, exported from top-level `loop`) runs every Workflow: Stages with typed inputs and trusted gates, routes, the Execution ledger and spans ([Compose Workflows as declarative definitions](../adr/compose-factory-workflows-as-declarative-definitions-over-a-stages-library-run-by-a-factory-runtime.md)). A typed agent (`TypedAgent`) is one reusable role: a profile, a request encoder and a reply decoder.
- Loop ships default implementations for git, the Copilot and Codex agent CLIs, and an in-memory session store. The replaceable seams are `CliRunner`, `GitService`, `DockerService`, and `SessionStore`; tests replace them with fakes ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)). `loop.shared` ships the Workflow-side Git helper, the process runner and the GitHub package (issues, pull requests, and the Spec and Ticket model) as concrete helpers ([Share the Spec and Ticket model](../adr/share-the-spec-and-ticket-model-in-workflows-shared-while-dev-keeps-its-delivery-policy.md)); implement_spec owns its delivery policy in `workflows/software_factory/workflows/implement_spec/policy.py`, and the factory's Stage library owns response decoding and publication.
- Loop ships no workflows: the user writes every workflow, `implement_spec` included; this repository's `workflows/` tree is only examples and test subjects, run from the clone and never installed ([Keep the software factory in this repository under `workflows/`](../adr/keep-software-factory-in-this-repository-as-an-unpackaged-root.md)).
- The runtime's definition language, Stage contract, Routes, Execution ledger, spans and escape hatches are recorded in [Factory Workflow Runtime](str-factory-workflow-runtime.md); this concept covers the library and the user-written workflow.
- The example `implement_spec` is a definition (`definition.py`) that places Stages and Flow fragments from the factory's Stage library; the Stages are independent of each other and of the definition, and per-repository variability is data on the routed `RepositoryTarget`, not a delegate.
- A workflow imports only the public `loop` API (and its public `loop.shared` modules) and is a runnable module: its entry point takes an argument list and returns an exit code, and a `__main__` guard passes that code to the process exit. Every Workflow builds its services by hand (no container) and `main(argv, *, services=None)` accepts a `Services` bundle, or injectable defaults, so tests substitute fakes.
- Loop ships no command: the user runs a workflow module through their own shell alias (for example `alias loop-implement-spec='python -m workflows.software_factory.workflows.implement_spec'`), with the harness as the current folder ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).
- The workflow's Python code carries its own settings: harness root, log dir, agent profiles, retry and failure bounds, prompts, and Loop hooks. `implement_spec` keeps entry-point settings in `__main__.py` and `settings.py`, its policy values in `policy.py`, and its role agents' prompt templates in `workflows/software_factory/agents/roles/<role>/prompt.md`. Command-line arguments are optional overrides; there is no Loop configuration file.
- Inside the library, each layer imports only the layers below it; the runtime sits on top beside the `Agent()` factory, shared helpers at the bottom, and nothing in Loop imports a workflow or a test.

## Rules

- MUST ship the builder, the runtime, the typed agent contract and default implementations through one public top-level `loop` API, with the shared GitHub package as the public `loop.shared.github`; a workflow MUST import only from them.
- MUST expose a contract only where Loop's own code calls a replaceable part (`CliRunner`, `GitService`, `DockerService`, `SessionStore`, the runtime's seams); the shared Git, process and GitHub helpers, including the Spec and Ticket model, are concrete helpers in `loop.shared`.
- MUST NOT ship workflows, workflow tests or a command in the `loop` package; the installed artifact is the library only.
- MUST let a user replace any shipped implementation (git, GitHub tracker, agent builder) with their own through the workflow's injected dependencies.
- MUST run every agent invocation through an `AgentClient` from the builder or an opened `Worktree`, usually behind a `TypedAgent`; a workflow MUST NOT call the provider CLI directly.
- MUST create the branch and worktree through `GitOptions` and a branch strategy, by default under the harness's `workspace` folder, and run the Workflow from the harness root so the worktree root is inside the process working directory.
- MUST give each workflow a Python entry point that owns its settings and wiring, returns the exit code, and runs through a `__main__` guard; a package may use `__main__.py` and keep its implementation in local modules.
- MUST let callers replace every external dependency by passing `Services` (or injectable defaults) to the entry point; no workflow module imports a dependency-injection container.
- MUST declare a workflow's settings and Loop hooks as code in the workflow module; a workflow MUST run with no command-line arguments beyond its required inputs and MUST NOT require a configuration file.
- MUST treat command-line arguments as optional overrides of the workflow's coded settings, owned by that workflow.
- MUST keep library tests in `tests/`, grouped by capability, and each Workflow's tests under `workflows/tests/<workflow tree>/`, mirroring its source folders; the shared `gh` fake lives in `tests/shared/fakes/` and workflow harnesses reuse or extend it.
- MUST NOT couple independent workflows through imports. A Workflow definition MUST NOT import another, `loop.runtime._engine` or the factory's `agents._kit`, and its policy module MUST import no process, Git or GitHub client.
- MUST keep the library layered by import direction (`runtime | factory` > `builder` > `agents` > `clis | docker | git` > `run` > `hooks` > `sessions` > `observe` > `shared`), and each workflow independent of sibling Workflows.
- MUST keep process execution (`git`, `gh`, agent CLIs) inside shipped implementations.
- MUST NOT run a workflow file from outside the harness-root workflows folder; running it executes its code.
- MUST place only logic whose business meaning is identical across workflows in Loop (`loop.shared`, the runtime); a factory Workflow's own rules and policy values stay in its `workflows/software_factory/workflows/<workflow>/` package, and reusable activity logic lives in the factory's Stage library, except the Spec and Ticket model, which lives in `loop.shared.github` while implement_spec keeps its delivery policy. The library returns the agent CLI's raw stdout ([Build agents with an agent builder](../adr/build-agents-with-an-agent-builder-over-profiles-strategies-and-session-stores.md)).
- SHOULD accept small duplication between workflows over extracting shared code that only looks similar; group code by feature, not by technical layer.

## Example

Sketch — not the implementation (derived from the example Workflows):

```text
src/  (package loop)                     # public API: builder, profiles, strategies, hooks, runtime
├── Agent(), AgentBuilder, Worktree      # worktree + host agent runs
├── AgentClient, AgentProfile, TypedAgent, copilot, codex, CliRunner
├── GitOptions, BranchStrategy, MergeToHeadStrategy, HeadStrategy, GitService
├── Stage, Flow, Workflow, Services, Ledger, run   # loop.runtime
├── LoopHook (three points), AgentCliHook
├── SessionStore, MemorySessionStore
└── shared/                              # git.py, process.py, github/ (Spec, Ticket, pull requests)
<harness root>/workflows/                # run from the clone; never installed
├── loop/plan_implement.py               # python -m workflows.loop.plan_implement
└── software_factory/
    ├── agents/, events/, stages/, flows/, infrastructure/   # role agents, Events, Stage library, fragments, factory-only infrastructure
    └── workflows/
        ├── implement_spec/                  # python -m workflows.software_factory.workflows.implement_spec
        │   ├── definition.py                # the definition over the Stage library
        │   ├── policy.py                    # hitl label, hand-off texts, subjects, work item, release rule
        │   ├── intake.py, settings.py, dry.py
        │   └── __main__.py                  # entry point, services, console and OpenTelemetry spans
        ├── pr_babysit/                      # python -m workflows.software_factory.workflows.pr_babysit
        └── address_once/                    # python -m workflows.software_factory.workflows.address_once
<harness root>/tests/                    # library tests by capability
<harness root>/workflows/tests/          # loop/ and software_factory/ Workflow tests
```

```python
def address_once(verify: Verify) -> Workflow[PullRequestWork]:
    return (
        Workflow("address_once").context(PullRequestWork)
        .stage(pull_requests.validate_origin())
        .stage(pull_requests.snapshot())
        .stage(feedback.select(trust=TRUST))
        .then(address.address_feedback(message=address_subject, verify=verify))
    )

def main(argv=None, *, services: Services | None = None) -> int:
    ...
    result = run(address_once(verify), work, services=services or build_services(), ledger=ledger, spans=spans)
    return result.exit_code

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

The user's aliases (`alias loop-implement-spec='python -m workflows.software_factory.workflows.implement_spec'`) call the module; Loop provides no command.

## Benefits and Trade-offs

**Benefits**

- A workflow is a short readable definition; each can choose its own topology (single loop, implement then review, planner then per-item implementers) from the runtime's four constructs.
- Defaults make a workflow tiny, while any piece (tracker, git, agent client) stays replaceable by an injected service.
- Workflows are testable with fakes at the process boundary.

**Trade-offs**

- The shipped implementations are part of the compatibility surface for user-supplied workflows.
- Workflows repeat some wiring (accepted, as Sandcastle repeats hooks across templates).
- Running workflow files from the harness workflows folder executes arbitrary code.

## Validation

- Workflow tests run against fakes at every process boundary.
- Architecture checks (import-linter) verify the library layering, that the factory and examples import only the public `loop` API, the factory layers (Workflow definitions over Flow fragments over Stages over Events over the runtime over role agents and shared infrastructure), that Stages, Workflows and role agents are mutually independent, that the runtime engine and agent kit are private, that no process starts in Stages, definitions or the runtime (the discovery scheduler excepted), that no dependency-injection container is imported, that the pure modules of the shared GitHub `issues` and `pull_requests` packages import no `gh` process code and that source never imports tests.
- Structural tests derive the rest from the code (`workflows/tests/software_factory/architecture/`): every definition builds and validates, Stage ids are unique, every Stage is used by a Workflow and every shared fragment by two, no Stage factory takes a `bool`, policy modules hold values and pure functions only, nothing uses the retired phase-reporting names, every Event and agent message round-trips through the ledger codec and each Workflow runs from its own import closure.
- The example workflows import only the public `loop` API; import-linter also keeps Loop free of any `workflows` or test import.

## Options Considered

| Option | Outcome |
|--------|---------|
| Horizontal domain, contracts, and infrastructure layers (Clean Architecture) | Rejected. The use-case-over-repository shape does not fit agent workflows; layers were mostly empty. |
| Feature-sliced workflows over a Workflow-side shared package with a Dishka composition root | Superseded by declarative Workflow definitions over the factory's Stage library, with services passed explicitly and no container ([Compose Workflows as declarative definitions](../adr/compose-factory-workflows-as-declarative-definitions-over-a-stages-library-run-by-a-factory-runtime.md), [Structure workflows as vertical slices](../adr/structure-workflows-as-vertical-slices-over-a-shared-package-wired-by-dishka.md)). |
| Runtime with fixed lifecycle phases; plugins fill slots | Rejected as the base. Removes loop duplication but cannot express planner, implementer, and reviewer flows. Loop adopts a validated declarative runtime instead ([Factory Workflow Runtime](str-factory-workflow-runtime.md)). |
| Pipeline of replaceable steps | Rejected. Implicit coupling through shared run state and harder tracing. Loop adopts a validated declarative runtime instead ([Factory Workflow Runtime](str-factory-workflow-runtime.md)). |
| Plugin folders with a `Command` contract, a composition-root `command.py`, and runtime-only imports for user workflows | Rejected. The prot2 prototype shows plain scripts over a public library suffice, without the command contract or per-workflow adapters. |
| Built-in workflows shipped in the `loop` package | Rejected ([Ship Loop as a workflow library](../adr/ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)). Repository settings and hooks would need an override channel back into the built-in. |
| Public library of an agent builder and default implementations; user-written workflows as plain runnable scripts run through the user's own alias | Superseded. The factory's declarative runtime replaced plain scripts as Loop's one Workflow architecture ([Compose Workflows as declarative definitions](../adr/compose-factory-workflows-as-declarative-definitions-over-a-stages-library-run-by-a-factory-runtime.md)). |
| **Public library of an agent builder, the declarative Workflow runtime, the typed agent contract and shared helpers; user-written Workflow definitions run through the user's own alias** | **Chosen.** Workflows own their Stages and policy; the runtime owns attempts, routes, recovery and reporting. |

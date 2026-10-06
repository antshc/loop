# CLI Runtime Workflow Architecture
**Type:** Architecture Pattern

## Purpose

Structure the Python CLI as a shared execution runtime plus self-contained workflows loaded as plugins. The runtime owns contracts and execution policy; each workflow owns its flow, its CLI command registration, and the adapters that bind runtime contracts to external systems.

New workflows are added by adding a workflow folder, inside the package or in a user-supplied directory. The CLI discovers them automatically and no central registry changes.

## Concept

A **plugin** is a unit the CLI discovers and loads dynamically. A **workflow** is a plugin: its flow, prompts, CLI command registration, and composition of adapters. It is the unit that Sandcastle calls a template (`simple-loop`, `sequential-reviewer`): self-contained, calling a shared core, owning its own control flow.

The **runtime** is the shared kernel. It defines contracts (CLI command, agent client, work tracker, execution store), the platform-neutral models those contracts return (`Ticket`, `Spec`, `PullRequest`), and execution policy (attempt cap, run context, exit codes). It never knows which workflows exist.

Built-in workflows ship inside the `loop` package and may use the shared `adapters/` package. Workflows loaded from user-supplied directories may import only `loop.runtime` and bring their own adapters ([ADR 0003](../adr/0003-ship-loop-as-a-workflow-library-with-no-built-in-workflows.md)).

| | Built-in workflow | User-supplied workflow |
|---|---|---|
| Location | `loop/workflows/<name>/` | `<user dir>/<name>/`, directory supplied by the user |
| Imported as | `loop.workflows.<name>.command` | `<user dir name>.<name>.command`, with the directory's parent added to `sys.path` |
| May import from `loop` | `runtime` and shared `adapters` | `runtime` only |
| Adapters | shared `adapters/`, or its own folder | its own folder |
| Architecture checks | import-linter | AST import check at load time or in the author's own tests |
| Trust | ships with the package | executes arbitrary code; trusted by configuring the directory |

```text
cli.py
  | discovers workflows/*/command.py (+ user-supplied workflow dirs)
  v
Workflow command.py   -- composition root: picks adapters, builds deps
  |        \
  v         v
Workflow flow  Adapters (implement runtime contracts)
  |              |
  v              v
Runtime contracts and policies
```

Dependency direction: `cli` → `runtime`; workflow → `runtime`; adapters → `runtime`. Nothing in `runtime` imports a workflow or an adapter.

Adapters used by more than one built-in workflow (for example GitHub, Copilot CLI, the process helper that runs `git`/`gh`/`copilot`) live in a shared `adapters/` package. Adapters specific to one workflow, including overrides of a shared adapter, live inside that workflow. In both cases only the workflow's `command.py` imports them.

## Rules

- MUST organize workflows as one folder per workflow, under the package's `workflows/` or under a user-supplied directory; each workflow maps to exactly one `loop` subcommand.
- MUST define the CLI command contract, adapter contracts, and execution policy in `runtime`.
- MUST define each platform-neutral model in the `runtime/contracts` module whose contract returns it; there is no separate domain layer.
- MUST define each contract as an `abc.ABC` with `@abstractmethod` operations and make every implementation inherit it.
- MUST make each workflow register its own command name, arguments, and help in `command.py`.
- MUST make the CLI discover commands from workflow folders; a new workflow MUST NOT require editing a central registry.
- MUST reject duplicate command names at discovery, across built-in and user-supplied workflows.
- MUST keep the workflow flow (`workflow.py`) dependent on runtime contracts only, never on concrete adapters.
- MUST construct adapters only in the workflow's `command.py`, which is that workflow's composition root.
- MUST keep `runtime` free of imports from workflows, adapters, and the CLI.
- MUST keep workflows independent: a workflow MUST NOT import another workflow.
- MUST keep the CLI limited to discovery, argument parsing, dispatch, and mapping the result to an exit code.
- MUST place an adapter in the shared `adapters/` package when more than one built-in workflow uses it.
- MUST keep process execution (`git`, `gh`, `copilot`) inside adapters; workflows see only contracts.
- MUST limit user-supplied workflows to importing `loop.runtime`; they bring their own adapters.
- MUST NOT name a user-supplied directory after an installed top-level module; its name becomes a top-level import name.
- MUST give built-in and user-supplied workflows the same `command.py` contract and the same discovery.
- MUST place only logic whose business meaning is identical across workflows (for example the attempt cap) in `runtime`.
- MUST NOT treat discovered workflow code from untrusted locations as safe; loading a workflow executes its code.
- SHOULD allow a workflow to own its control flow (runtime-provided loop or its own loop) rather than forcing one lifecycle.
- SHOULD accept small duplication between workflows over extracting shared code that only looks similar.
- SHOULD load additional workflow directories only from explicitly user-supplied, trusted directories.
- SHOULD check user-supplied workflows' imports statically (AST scan) because import-linter cannot see code outside the package.

## Example

```text
src/loop/
├── runtime/
│   ├── contracts/
│   │   ├── command.py            # Command ABC
│   │   ├── agent_client.py       # AgentClient, AgentRunResult
│   │   ├── work_tracker.py       # WorkTracker, Ticket, Spec, PullRequest
│   │   └── execution_store.py    # attempt records
│   ├── discovery.py              # finds workflows/*/command.py
│   ├── attempts.py               # attempt cap and reset policy
│   ├── context.py                # RunContext: repo, log dir, dry-run, logger
│   └── errors.py                 # exit codes
├── adapters/                     # shared implementations of runtime contracts
│   ├── process.py                # runs git, gh, copilot
│   ├── copilot_cli.py
│   └── github/
├── workflows/
│   └── dev/
│       ├── command.py            # name, arguments, adapter wiring
│       ├── workflow.py           # flow
│       └── prompt.md
└── cli.py
```

A user-supplied directory holds workflows of the same shape. It may override a shared adapter inside a workflow's own folder:

```text
~/loop-workflows/                 # passed to the CLI; name must not collide with an installed module
├── __init__.py
└── review/
    ├── command.py                # imports loop.runtime only
    ├── workflow.py
    ├── github_tracker.py         # own adapter implementing the runtime contract
    └── prompt.md
```

CLI commands map one-to-one to workflow folders:

```text
loop dev           → loop/workflows/dev
loop review        → ~/loop-workflows/review
```

The command contract:

```python
class Command(ABC):
    name: str
    help: str

    @abstractmethod
    def configure(self, parser: argparse.ArgumentParser) -> None: ...

    @abstractmethod
    def run(self, args: argparse.Namespace, ctx: RunContext) -> int: ...
```

A workflow registers its command and composes its adapters:

```python
class DevCommand(Command):
    name = "dev"
    help = "Run the autonomous dev loop on open specs."

    def configure(self, parser): ...

    def run(self, args, ctx) -> int:
        deps = Deps(
            agent=CopilotCli(ctx),
            tracker=GitHubSpecTracker(ctx),
            store=FileExecutionStore(ctx.log_dir),
        )
        return workflow.run(args, ctx, deps)


command = DevCommand()
```

Discovery reads each workflow's exported `command`. A root is imported by package name when it is part of the package; otherwise its parent joins `sys.path` and the directory name is the package. Roots merge into one command table, so the duplicate check spans built-in and user-supplied workflows:

```python
def discover_commands(
    root: Path,
    package: str | None = None,
    into: dict[str, Command] | None = None,
) -> dict[str, Command]:
    root = root.resolve()
    if package is None:
        if str(root.parent) not in sys.path:
            sys.path.insert(0, str(root.parent))
        package = root.name
    commands = {} if into is None else into
    for entry in sorted(root.iterdir()):
        if (entry / "command.py").is_file():
            cmd = import_module(f"{package}.{entry.name}.command").command
            if cmd.name in commands:
                raise ValueError(f"duplicate command: {cmd.name}")
            commands[cmd.name] = cmd
    return commands
```

The CLI discovers the built-in root first, then each user-supplied directory, builds one subparser per discovered command, and returns `command.run(...)` as the process exit code.

## Options Considered

| Option | Outcome |
|--------|---------|
| Vertical slices over domain, contracts, and infrastructure layers (Clean Architecture) | Rejected. The use-case-over-repository shape does not fit agent workflows; layers were mostly empty. |
| Runtime with fixed lifecycle phases; plugins fill slots | Rejected as the base. Removes loop duplication but cannot express planner, implementer, and reviewer flows. |
| Pipeline of replaceable steps | Rejected. Implicit coupling through shared run state and harder tracing. |
| **Runtime kernel plus self-contained workflow plugins with command registration and auto-discovery** | **Chosen.** Workflows own control flow, as Sandcastle templates do. |

## Benefits and Trade-offs

**Benefits**

- Adding a workflow is adding a folder; nothing central changes.
- Each workflow can choose its own topology: single loop, implement then review, or planner then parallel implementers.
- The runtime stays small and testable against contracts.
- Workflows are testable with fake adapters through their explicit dependencies.

**Trade-offs**

- Workflows repeat some setup code (accepted, as Sandcastle repeats hooks across templates).
- Discovery hides imports from static analysis; architecture checks must read source files.
- Each workflow's `command.py` is a composition root, so wiring is distributed rather than central.
- Loading workflows from user-supplied directories executes arbitrary code.
- A user-supplied directory name becomes a top-level module name, so a collision with an installed module shadows or breaks it.
- A user-supplied workflow needing GitHub or Copilot access reimplements those adapters; the shared ones stay internal and free to change.

## Validation

Architecture review should verify that:

- `runtime` and `cli` import no workflow or adapter;
- no workflow imports another workflow;
- `workflow.py` imports no adapter; only `command.py` constructs adapters;
- each workflow folder exposes one `command` with a unique name;
- adding a workflow requires no edits outside its folder;
- user-supplied workflows import nothing from `loop` except `loop.runtime`, checked by an AST import scan;
- built-in and user-supplied workflows sharing a command name fail discovery;
- code in `runtime` is shared because of business meaning, not code reuse alone.

Enforce these with import-linter `forbidden` and `independence` contracts.

## References

- [Sandcastle agent invocation and extension points](../research/sandcastle-agent-invocation-and-extension-points.md)
- [Sandcastle worktrees and tasks](../research/sandcastle-worktrees-and-tasks.md)

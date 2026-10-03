# CLI Runtime Slice Architecture
**Type:** Architecture Pattern

## Purpose

Structure the Python CLI as a shared execution runtime plus self-contained slices. The runtime owns contracts and execution policy; each slice owns one workflow, its CLI command registration, and the adapters that bind runtime contracts to external systems.

New workflows are added by adding a slice folder. The CLI discovers them automatically and no central registry changes.

## Concept

A **slice** is one workflow: its flow, prompts, CLI command registration, and composition of adapters. It is the unit that Sandcastle calls a template (`simple-loop`, `sequential-reviewer`): self-contained, calling a shared core, owning its own control flow.

The **runtime** is the shared kernel. It defines contracts (CLI command, agent client, work tracker, execution store) and execution policy (attempt cap, run context, exit codes). It never knows which slices exist.

```text
cli.py
  | discovers slices/*/command.py
  v
Slice command.py   -- composition root: picks adapters, builds deps
  |        \
  v         v
Slice flow  Adapters (implement runtime contracts)
  |              |
  v              v
Runtime contracts and policies
```

Dependency direction: `cli` → `runtime`; slice → `runtime`; adapters → `runtime`. Nothing in `runtime` imports a slice or an adapter.

Adapters used by more than one slice (for example GitHub, Copilot CLI) live in a shared `adapters/` package. Adapters specific to one slice live inside that slice. In both cases only the slice's `command.py` imports them.

## Rules

- MUST organize workflows as one folder per slice under `slices/`; each slice maps to exactly one CLI command.
- MUST define the CLI command contract, adapter contracts, and execution policy in `runtime`.
- MUST define each contract as an `abc.ABC` with `@abstractmethod` operations and make every implementation inherit it.
- MUST make each slice register its own command name, arguments, and help in `command.py`.
- MUST make the CLI discover commands from slice folders; a new slice MUST NOT require editing a central registry.
- MUST reject duplicate command names at discovery.
- MUST keep the slice flow (`slice.py`) dependent on runtime contracts only, never on concrete adapters.
- MUST construct adapters only in the slice's `command.py`, which is that slice's composition root.
- MUST keep `runtime` free of imports from slices, adapters, and the CLI.
- MUST keep slices independent: a slice MUST NOT import another slice.
- MUST keep the CLI limited to discovery, argument parsing, dispatch, and mapping the result to an exit code.
- MUST place an adapter in the shared `adapters/` package when more than one slice uses it.
- MUST place only logic whose business meaning is identical across slices (for example the attempt cap) in `runtime`.
- MUST NOT treat discovered slice code from untrusted locations as safe; loading a slice executes its code.
- SHOULD allow a slice to own its control flow (runtime-provided loop or its own loop) rather than forcing one lifecycle.
- SHOULD accept small duplication between slices over extracting shared code that only looks similar.
- SHOULD load additional slice roots only from explicitly configured, trusted directories.

## Example

```text
src/afk/
├── runtime/
│   ├── contracts/
│   │   ├── command.py            # Command ABC
│   │   ├── agent_client.py       # AgentClient, AgentRunResult
│   │   ├── work_tracker.py       # platform-neutral work items and threads
│   │   └── execution_store.py    # attempt records
│   ├── discovery.py              # finds slices/*/command.py
│   ├── attempts.py               # attempt cap and reset policy
│   ├── context.py                # RunContext: repo, log dir, dry-run, logger
│   └── errors.py                 # exit codes
├── adapters/                     # shared implementations of runtime contracts
│   ├── copilot_cli.py
│   └── github/
├── slices/
│   ├── dev/
│   │   ├── command.py            # name, arguments, adapter wiring
│   │   ├── slice.py              # flow
│   │   └── prompt.md
│   ├── fix_prs/
│   └── address_prs/
└── cli.py
```

CLI commands map one-to-one to slice folders:

```text
afk dev            → slices/dev
afk fix-prs        → slices/fix_prs
afk address-prs    → slices/address_prs
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

A slice registers its command and composes its adapters:

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
        return slice.run(args, ctx, deps)


command = DevCommand()
```

Discovery reads each slice's exported `command`:

```python
def discover_commands(root: Path) -> dict[str, Command]:
    commands: dict[str, Command] = {}
    for entry in sorted(root.iterdir()):
        if (entry / "command.py").is_file():
            cmd = import_module(f"afk.slices.{entry.name}.command").command
            if cmd.name in commands:
                raise ValueError(f"duplicate command: {cmd.name}")
            commands[cmd.name] = cmd
    return commands
```

The CLI builds one subparser per discovered command and returns `command.run(...)` as the process exit code.

## Options Considered

| Option | Outcome |
|--------|---------|
| Vertical slices over domain, contracts, and infrastructure layers | Rejected. The use-case-over-repository shape does not fit agent workflows; layers were mostly empty. |
| Runtime with fixed lifecycle phases; plugins fill slots | Rejected as the base. Removes loop duplication but cannot express planner, implementer, and reviewer flows. |
| Pipeline of replaceable steps | Rejected. Implicit coupling through shared run state and harder tracing. |
| **Runtime kernel plus self-contained slices with command registration and auto-discovery** | **Chosen.** Slices own control flow, as Sandcastle templates do. |

## Benefits and Trade-offs

**Benefits**

- Adding a workflow is adding a folder; nothing central changes.
- Each slice can choose its own topology: single loop, implement then review, or planner then parallel implementers.
- The runtime stays small and testable against contracts.
- Slices are testable with fake adapters through their explicit dependencies.

**Trade-offs**

- Slices repeat some setup code (accepted, as Sandcastle repeats hooks across templates).
- Discovery hides imports from static analysis; architecture checks must read source files.
- Each slice's `command.py` is a composition root, so wiring is distributed rather than central.
- Loading slices from external directories executes arbitrary code.

## Validation

Architecture review should verify that:

- `runtime` and `cli` import no slice or adapter;
- no slice imports another slice;
- `slice.py` imports no adapter; only `command.py` constructs adapters;
- each slice folder exposes one `command` with a unique name;
- adding a slice requires no edits outside its folder;
- code in `runtime` is shared because of business meaning, not code reuse alone.

Enforce these with import-linter `forbidden` and `independence` contracts.

## References

- [Sandcastle agent invocation and extension points](../research/sandcastle-agent-invocation-and-extension-points.md)
- [Sandcastle worktrees and tasks](../research/sandcastle-worktrees-and-tasks.md)

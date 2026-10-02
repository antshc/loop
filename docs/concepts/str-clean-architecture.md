# CLI Vertical Slice Architecture
**Type:** Architecture Pattern

## Purpose

Structure a Python CLI application so business capabilities are implemented as independent vertical slices while Clean Architecture dependency boundaries remain explicit.

The CLI acts only as an input/output adapter. Each command dispatches to one slice that owns the use-case behavior. Shared business concepts and infrastructure remain outside individual slices.

## Concept

The application is organized primarily by **business capability and use case**, not by technical layer.

A vertical slice contains the application behavior required to execute one use case, such as `create_user`, `generate_report`, or `cleanup_snapshots`.

The CLI translates command-line input into slice input, invokes the slice, and renders the result.

The dependency direction is:

```text
CLI
 ↓
Vertical Slice
 ↓
Domain / Contracts
 ↑
Infrastructure
```

The main responsibilities are:

- `cli` — command parsing, input/output formatting, exit codes.
- `slices` — use-case orchestration and application behavior.
- `domain` — shared business entities, value objects, and rules used by multiple slices.
- `contracts` — abstractions required by slices to interact with external capabilities.
- `infrastructure` — implementations of contracts such as repositories, APIs, filesystem, and databases.
- `bootstrap` — composition root that constructs infrastructure and injects dependencies into slices.

A slice may remain in one module when simple and be split into command, handler, models, or validation modules only when complexity requires it.

## Rules

- MUST organize application behavior by business capability and use case under `slices`.
- MUST keep CLI commands thin and limited to parsing input, invoking a slice, and presenting output.
- MUST keep business behavior out of CLI command handlers.
- MUST make each CLI operation invoke one owning vertical slice.
- MUST make slices depend on abstractions rather than concrete infrastructure implementations.
- MUST construct infrastructure dependencies in the application composition root.
- MUST keep infrastructure implementations outside individual slices when they are shared or represent external systems.
- MUST place business concepts used by multiple slices in the shared domain.
- MUST keep behavior specific to one use case inside its owning slice.
- MUST NOT create horizontal application-wide `handlers`, `commands`, `queries`, or `services` directories for slice-specific behavior.
- MUST NOT make domain or slice code depend on the CLI.
- MUST NOT make domain or slice code depend directly on concrete infrastructure when an external boundary requires abstraction.
- SHOULD keep a simple slice in a single module until additional structure improves readability.
- SHOULD mirror business capability grouping between CLI commands and slices where this keeps navigation predictable.

## Example

```text
src/
└── myapp/
    ├── cli/
    │   ├── main.py
    │   └── commands/
    │       ├── users.py
    │       └── reports.py
    │
    ├── slices/
    │   ├── users/
    │   │   ├── create_user/
    │   │   │   └── slice.py
    │   │   └── get_user/
    │   │       └── slice.py
    │   │
    │   └── reports/
    │       └── generate_report/
    │           └── slice.py
    │
    ├── domain/
    │   └── user.py
    │
    ├── contracts/
    │   └── user_repository.py
    │
    ├── infrastructure/
    │   └── user_repository.py
    │
    └── bootstrap.py
```

CLI commands map directly to slices:

```text
users create       → users/create_user
users get          → users/get_user
reports generate   → reports/generate_report
```

A CLI adapter remains thin:

```python
@app.command()
def create(name: str, email: str) -> None:
    deps = create_dependencies()

    result = create_user.execute(
        CreateUser(name=name, email=email),
        deps.create_user,
    )

    typer.echo(result.user_id)
```

The slice owns the use-case behavior:

```python
@dataclass(frozen=True)
class CreateUser:
    name: str
    email: str


@dataclass
class Dependencies:
    users: UserRepository


def execute(command: CreateUser, deps: Dependencies) -> User:
    user = User(
        name=command.name,
        email=command.email,
    )

    deps.users.add(user)
    return user
```

The composition root binds abstractions to infrastructure:

```python
def create_dependencies() -> AppDependencies:
    database = create_database()

    return AppDependencies(
        users=SqlUserRepository(database),
    )
```

## Benefits and Trade-offs

**Benefits**

- Keeps all behavior for a use case close together.
- Makes CLI commands easy to trace to application behavior.
- Reduces coupling between unrelated use cases.
- Preserves dependency inversion around databases, APIs, filesystems, and other external systems.
- Allows individual slices to evolve without introducing application-wide technical layers.
- Makes slice-level testing straightforward because dependencies are explicit.

**Trade-offs**

- Some patterns or small data structures may be repeated between slices.
- Developers must distinguish genuinely shared domain behavior from behavior that only appears similar across slices.
- Very small applications may initially have more architectural boundaries than strictly necessary.

## Validation

Architecture review should verify that:

- CLI commands contain no business behavior.
- each command has a clear owning slice;
- slice-specific behavior is not moved into global technical-layer folders;
- external dependencies enter slices through explicit contracts;
- infrastructure dependencies point inward through those contracts;
- shared domain code is shared because of business meaning rather than code reuse alone.
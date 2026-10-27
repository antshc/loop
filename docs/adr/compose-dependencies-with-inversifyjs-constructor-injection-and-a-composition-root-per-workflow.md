# Compose dependencies with InversifyJS constructor injection and a composition root per Workflow

The Python Workflows took every external dependency as an optional keyword parameter of `main` and defaulted it to the shipped implementation, so tests substituted fakes. The port keeps that contract (`main(argv, dependencies)` with optional overrides) but wires the defaults through a dependency-injection container, so a Workflow with many seams has one place that binds them.

- **Container.** InversifyJS (`inversify` and `reflect-metadata`), a regular dependency; only the telemetry packages stay optional. The decorator compiler options `experimentalDecorators` and `emitDecoratorMetadata` are enabled in `tsconfig.json`.
- **Injection.** Classes receive their collaborators through the constructor, each parameter marked with `@inject(token)`. Shared services bind in singleton scope.
- **Tokens.** One `Symbol.for(...)` token per seam, bound to an interface or function type, never to a concrete class.
- **Composition root.** One per Workflow (`compose` in `plan_implement`, `composeDev` in `dev`, and so on) and the only place that binds implementations to tokens.
- **Overrides.** `main(argv, dependencies)` accepts a typed object whose members are named after the tokens; the composition root first binds every shipped implementation, then rebinds each token that has an override.
- **Loop.** Loop may use the container internally, for example behind `Agent()`, but is not required to; today `Agent()` wires its defaults by hand. The container is not part of the public API, and the authoring API, builders, and injectable seams (`CliRunner`, `GitService`, `DockerService`, `SessionStore`) are unchanged.

## Considered Options

- **Wire defaults by hand in each `main`** — rejected: the Python shape of one optional parameter per dependency grows with the number of seams and repeats the default expressions.
- **Expose the container so Workflows or tests resolve services from it** — rejected: it would make the container part of the compatibility surface; tests and callers override through `main`'s typed dependencies instead.
- **Require Loop's own classes to use the container** — rejected: the library's constructors stay plain so users who write Workflows without a container are unaffected.

## Consequences

- The decorator compiler options are toolchain setup, not a build step; Bun executes the TypeScript source directly.
- Workflow modules import `reflect-metadata` and `inversify`; the Loop library itself imports neither, so a Workflow that wires its dependencies by hand is unaffected.
- Adding a seam means adding a token, an override member, and a binding in the composition root; a missing binding fails when the Workflow is resolved, not at the first use.
- Tests construct a Workflow through its composition root with fakes bound to the same tokens, so they exercise the real wiring.

See [Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md) for how Workflows are composed and run.

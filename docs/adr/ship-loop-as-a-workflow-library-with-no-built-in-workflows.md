# Ship Loop as a workflow library with no built-in workflows

Loop is a library for composing agent workflows: a workflow is user-owned Python that wires an agent builder, Loop's Workflow runtime, Git and GitHub helpers, and owns its policy. The Python package is named `loop` and ships no command and no workflows; the user writes every workflow, `implement_spec` included, on the public `loop` API, which exports the contracts and the shipped implementations so a workflow never reimplements them. This repository keeps its factory and example workflows under `workflows/`, run from the clone, as examples and test subjects.

Contracts (`abc.ABC` or `Protocol`) exist only where Loop's own code calls a replaceable part: `CliRunner`, `GitService`, `DockerService`, `SessionStore`, and the runtime's seams (`Spans`, the typed agent's workspace). Loop also ships reusable helpers no Loop code calls — the Workflow-side Git helper, the process runner and the GitHub clients with the Spec and Ticket model (`loop.shared`) — as concrete classes with no contract; a user replaces one by calling their own client from their own workflow. Workflow policy, Stages, role agents and prompts stay with the Workflow.

## Considered Options

- **A platform-neutral `WorkTracker` ABC that `GitHubClient` inherits** — rejected: the workflow is the only caller and is user-owned, so the contract constrains nothing.
- **A `typing.Protocol` for the tracker** — rejected: same reason; a structural type nobody but the workflow checks adds surface without value.
- **A single `loop` console command whose subcommands are the discovered workflows (`loop dev --option <value>`)** — rejected: a dispatch command needs workflow discovery and a registry contract, while a plain script run through the user's own alias needs neither.
- **Keep the `afk` import package, with a `ralph` or `afk` console command, or one console script per workflow (`afk_dev`, `afk_fix_prs`, `afk_address_prs`)** — rejected: Ralph is the orchestrator, not the product, and the names do not match the product Loop.
- **Ship built-in workflows (`implement_spec`) inside the `loop` package, resolved before the harness folder** — rejected: workflows are user-owned; repository-specific settings and hooks belong in the user's file, and a built-in `implement_spec` would need an override channel back into it.
- **Expose only `loop.runtime` to user-supplied workflows; they bring their own adapters** — rejected: every workflow needing GitHub or Copilot access reimplements the clients.
- **Keep the GitHub, Git and process helpers and the Workflow runtime Workflow-side, with Loop limited to agent and worktree primitives for plain scripts** (the previous decision) — rejected: the runtime and helpers mean the same to every Workflow, and the factory's declarative architecture replaced the plain-script model.

## Consequences

- A `pip install` alone yields no runnable Workflow and no command; a Workflow runs only once the user has an entry point such as `workflows/software_factory/workflows/implement_spec/__main__.py` and their own shell alias for it (e.g. `alias loop-dev='python -m workflows.software_factory.workflows.implement_spec'`).
- The repository's example and factory Workflows are not part of the compatibility surface.
- The import path, `pyproject.toml` project name, and any `afk_*` invocations in skills and docs follow the `loop` package name.
- The shipped implementations (agent builder, runtime, typed agent contract, shared helpers, stores) are part of the compatibility surface; breaking them breaks user-supplied workflows.
- Importing anything other than the public `loop` API and its public `loop.shared` modules from a workflow is a violation.
- Running a workflow file executes its code; the harness workflows folder is trusted by the act of placing a file there.

See [Loop Library Workflow Architecture](../concepts/str-loop-library-workflow-architecture.md) for how workflows are composed and run.

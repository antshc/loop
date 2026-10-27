<p align="left">
  <img src="loop_icon.png" alt="Loop" width="128" height="128">
</p>

# Loop

TypeScript library, run by Bun, for composing autonomous coding-agent Workflows on Git worktrees.

Use Loop to run Copilot CLI or Codex from TypeScript, manage branches and worktrees,
and coordinate multiple agent steps. You own the prompts, control flow, and
interpretation of results; Loop provides the execution and lifecycle building blocks.
It ships no command and no built-in Workflows.

[Installation](#installation) | [Quick start](#quick-start) | [Running the examples](#running-the-examples) | [Examples](#examples) | [Documentation](#documentation) | [Contributing](#contributing)

## Features

- **Composable agents:** choose a CLI, model, and reasoning effort per agent profile.
- **Reusable TypeScript profiles:** import instructions, tool permissions, directories,
  environment, and timeouts into your Workflows. See [profile examples](USAGE.md#reusable-agent-profiles).
- **Git lifecycle management:** use a named-branch worktree, a temporary branch
  merged back into HEAD, or the repository directory directly.
- **Multi-step Workflows:** share a worktree across agents and optionally resume sessions.
- **Lifecycle commands:** run host shell commands at `worktree-ready`,
  `worktree-removing`, and `run-finished` Loop hook points.
- **Ctrl-C and SIGTERM handling:** the agent's whole process tree is ended and
  worktree cleanup runs; exit codes are 130 and 143.
- **Dry runs:** log Git and agent operations before enabling real execution.

## Requirements

- [Bun](https://bun.sh) 1.4.4 or newer, for both the library and the example Workflows. Node.js is unsupported.
- Git for repository and worktree operations.
- [Copilot CLI](https://github.com/github/copilot-cli) or
  [Codex CLI](https://github.com/openai/codex), installed and authenticated for live agent runs.
- [GitHub CLI](https://cli.github.com/) (`gh`), authenticated, only for examples
  that interact with GitHub.
- Optional: the OpenTelemetry packages, only to export telemetry over OTLP.

## Installation

Install from this repository; there is no package index. TypeScript source is consumed
directly, so there is no build step. In a shell:

```sh
git clone https://github.com/antshc/loop.git
cd loop
bun install
```

To use Loop from your own harness, add it as a Bun package pinned to a revision;
`bun.lock` pins it further:

```sh
bun add github:antshc/loop#<revision>
```

The repository revision determines the installed version. To update, move the pinned
revision and reinstall. Telemetry export needs the optional OpenTelemetry packages
(`@opentelemetry/api`, `@opentelemetry/sdk-trace-base`, `@opentelemetry/sdk-metrics`,
`@opentelemetry/resources`, `@opentelemetry/exporter-trace-otlp-http`,
`@opentelemetry/exporter-metrics-otlp-http`); requesting OTLP export without them fails
at startup with an install message.

## Quick start

Save this as `my-workflow.ts` in your harness and run it from the Git repository you want
to work on. It starts in **dry-run mode**: Git and agent operations are logged, not executed.

```ts
import { Agent, AgentProfile, AgentRequest, BranchStrategy, configureLogging, copilot, GitOptions } from 'loop';

configureLogging();
const options = new GitOptions({ strategy: new BranchStrategy('loop/fix-tests') });

await Agent({ dryRun: true })
  .withGit(options)
  .open(async (worktree) => {
    const agent = worktree.agent(new AgentProfile(copilot));
    const result = await agent.run(new AgentRequest('Fix the failing tests, verify, and commit. Do not push.'));
    console.log(result.output);
  });
```

```sh
bun my-workflow.ts
```

To run it for real, replace `Agent({ dryRun: true })` with `Agent()`.
The example uses the CLI's default model and reasoning effort. It creates or
reuses `loop/fix-tests`, starting a new branch from HEAD, and runs the agent in
`.worktrees/loop/fix-tests`. Loop removes clean worktrees on success or failure;
the local branch and its commits remain. Dirty worktrees are preserved, and a
successful body leaving edits rejects with `WorktreeError`. Loop never commits or pushes
automatically: the agent or Workflow must explicitly commit verified changes.

`result.output` contains the agent's output text, not a validated task outcome.
Your Workflow must interpret it and verify the work.

> **Execution safety:** agents run on the host with your user permissions.
> Worktrees separate code changes; they are not a security sandbox. The default
> Copilot adapter allows all tools. Use trusted repositories and prompts, and
> review changes before publishing them.

## Running the examples

Each Workflow is a plain script run by Bun from the harness root, usually through your own alias:

```sh
bun workflows/dev/main.ts --help
bun workflows/plan_implement.ts "Add cart quantity validation"
bun workflows/pr_babysit/main.ts --config babysit.local.json
bun workflows/workflow_reporting.ts --visualize

alias loop-dev='bun /path/to/harness/workflows/dev/main.ts'
```

Each Workflow parses its own flags, prints generated help, and exits with code 2 and usage on bad input.
See [USAGE.md](USAGE.md) for the flags of each Workflow.

## Examples

The examples live in this repository and are not part of the `loop` package.

| Example | Purpose |
|---|---|
| [Library usage](USAGE.md#library-examples) | One-shot runs, profiles, shared worktrees, sessions, and Loop hooks |
| [PR scheduler and babysitter](workflows/pr_babysit/README.md) | Discover user-authored PRs and address reviews/CI with JSON recovery and scoped telemetry |
| [Plan and implement](workflows/plan_implement.ts) | Plan and implement with different models on one worktree |
| [Dev Workflow guide](USAGE.md#dev-workflow) | Deliver GitHub Tickets, configure multiple repositories, and customize prompts |
| [Multi-repository Workflow](USAGE.md#different-agent-workflows-per-repository) | Route Specs to several repositories, each with its own agent steps |
| [Workflow reporting](workflows/workflow_reporting.ts) | A fake delivery that demonstrates optional phase reporting, the terminal table, and spans |

The dev Workflow can create commits, push branches, open draft pull requests,
and update Tickets. Read its configuration guide before running it.

## Platform support and known gaps

Signal delivery, process-group kill, and OS file locking are verified on Linux; macOS and Windows
behavior is untested. Telemetry is verified against in-memory exporters, not a real collector, and
the OpenTelemetry packages are not officially supported on Bun. Docker agent behavior under Bun is
unspecified. `pr_babysit`'s single-instance lock uses Bun's FFI, which Bun marks experimental.

## Documentation

- [Usage guide](USAGE.md): advanced examples, logging, and dev Workflow configuration.
- [Domain glossary](CONTEXT.md): project terminology.
- [Architecture](ARCHITECTURE.md): codebase structure, design decisions, and concepts.
- [System context](docs/building-blocks/system-context.md): context and container diagrams.
- [Bun and TypeScript decision](docs/adr/port-loop-and-workflows-to-typescript-on-bun-in-one-cutover.md): runtime, distribution, and versioning.

## Contributing

From the repository root:

```sh
bun install
bun run test
```

`bun run test` runs ESLint, the TypeScript compiler, and `bun test`, which covers library tests,
example Workflow tests, and the dependency-cruiser architecture checks. `bun run lint` and
`bun run typecheck` run the first two alone. See the [test strategy](docs/concepts/ops-test-strategy.md)
for testing boundaries.

For compatibility changes, run the suite on the pinned minimum Bun and on the latest Bun; see
[Runtime compatibility](docs/concepts/ops-test-strategy.md#runtime-compatibility).

For bug reports and feature requests, [open an issue](https://github.com/antshc/loop/issues).
Include reproduction steps and relevant Bun and agent CLI versions for bugs;
remove credentials and sensitive data from logs before sharing them.

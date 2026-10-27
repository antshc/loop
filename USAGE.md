# Usage guide

Start with the [installation and quick start](README.md#installation).
This guide covers the example dev Workflow and advanced library usage.
Run repository examples from the repository root with Bun.

Snippets import Loop as `loop`, the package name in your harness; inside this repository the
example Workflows import `../src/loop/index.ts`, the same public entry point.

- [Dev Workflow](#dev-workflow)
- [Optional Workflow Builder](#optional-workflow-builder)
- [Logging](#logging)
- [Telemetry (optional)](#telemetry-optional)
- [Library examples](#library-examples)
- [Reusable agent profiles](#reusable-agent-profiles)

## Reusable agent profiles

Define an agent once in TypeScript and import it into any Workflow. A profile owns
reusable instructions, CLI settings, tool permissions, and execution defaults;
each `AgentRequest` supplies the task.

```ts
// agents/developer.ts
import { join } from 'node:path';
import { AgentProfile, Instructions, Permissions, copilot } from 'loop';

const ROOT = join(import.meta.dir, '..');

export const developer = new AgentProfile(copilot, {
  name: 'developer',
  // model: 'your-model', // Omit to use the installed CLI's default.
  reasoningEffort: 'high',
  instructions: Instructions.fromFile(join(ROOT, 'prompts', 'developer.md')),
  cwd: ROOT,
  addDirs: [join(ROOT, 'docs')],
  permissions: new Permissions({ tools: 'allow-all', denyTools: ['shell(git push)'] }),
  env: { DOTNET_ENVIRONMENT: 'Development', LOG_LEVEL: 'debug' },
  timeoutSec: 1800,
  args: [],
});
```

```ts
import { Agent, AgentRequest, BranchStrategy, GitOptions } from 'loop';
import { developer } from './agents/developer.ts';

const request = new AgentRequest('Add cart quantity validation, verify, and commit.');
const result = await Agent().create(developer).run(request);

await Agent()
  .withGit(new GitOptions({ strategy: new BranchStrategy('cart-validation') }))
  .open(async (worktree) => worktree.agent(developer).run(request));
```

Instruction sources can also be literal text or a public raw-text URL:

```ts
new AgentProfile(copilot, { instructions: 'Implement the task and verify the result.' });
new AgentProfile(copilot, { instructions: Instructions.fromUrl('https://example.com/developer.md') });
```

Defining or importing a profile does not read its instruction source. Client
creation reads it once; each client keeps a snapshot. Files require absolute
paths. HTTP(S) URLs must return raw UTF-8 text, with a 10-second fetch timeout and
a 1 MiB response limit. Empty content, HTML responses, decoding failures, and
read/fetch errors reject with `InstructionsError` before any Agent session is registered.
Pin remote sources to immutable revisions when reproducibility matters.

Every turn, including resumed turns, receives the instructions followed by the
task. This is ordinary prompt composition, without provider-native system-message
priority. Template placeholders remain literal unless the Workflow explicitly
calls `new Prompt(template, args).render()`. Existing profiles without instructions send the
original request unchanged. Dry runs do not read file or URL instruction sources.

| Setting | Resolution |
|---|---|
| `cwd` | Profile overrides builder defaults for direct execution and the root of `withGit().create()` execution. An already opened workspace keeps its directory. |
| `addDirs`, `env`, `timeoutSec` | Configured profile fields replace builder defaults; `null` inherits them. Empty directories or environment objects explicitly clear those overrides. |
| Explicit `run(request, context)` | Replaces the execution context completely, preserving existing behavior. Git/Docker wrappers may transform it before execution. |
| Harness start mode | Applies after target-directory resolution; adds the target directory and prefixes its navigation instruction. |
| Process environment | Inherits the host environment, then applies configured overrides; Loop's native hook wiring takes precedence. |

Profile directories must be absolute. Process timeouts must be finite and
positive; expiration rejects with `ProcessTimeoutError`. Environment values are
copied when configured and are not included in Loop's execution logs.

`new Permissions({ tools: 'allow-all' })` maps to Copilot's `--allow-all-tools`, not its
broader `--allow-all` flag. Alternatively, pass an array of native approval patterns,
such as `new Permissions({ tools: ['read', 'shell(git diff)'] })`. These patterns configure
tool approval, not an exclusive list of available tools or an OS sandbox.
`denyTools` uses native deny patterns, which take precedence over approvals.
Explicit policies remove Copilot's default broad permission flag. Conflicting
raw tool-permission arguments throw `ValueError`; unsupported policies throw
`UnsupportedPermissions` at client creation. Loop's Codex adapter currently rejects
typed tool policies; use its native `args` for provider-specific settings instead.
When `permissions` is `null`, the existing adapter defaults remain unchanged.

Git strategies, Docker lifecycle, session storage, observation, and hooks remain
builder-owned. Additional CLI-specific settings remain available through `args`.

## Dev Workflow

The self-contained [`workflows/dev/`](workflows/dev/) folder is an example,
not part of the `loop` package. It uses GitHub Specs and Tickets to drive
agent work, commits, draft pull requests, and Ticket updates.

Before running it, configure `HARNESS` and target repositories in
[`repositories.ts`](workflows/dev/repositories.ts). The supplied configuration
targets `antshc/loop`; it is not a read-only demo.

```sh
gh auth status
copilot --version
bun workflows/dev/main.ts
```

Optionally run it through your own alias, from the harness root:

```sh
alias dev='bun /path/to/harness/workflows/dev/main.ts'
dev --log-level DEBUG
```

Flags: `--log-dir`, `--log-level`, `--visualize`, `--spec`, `--no-otel`, `--otlp-endpoint`,
`--fake`, `--dry`, `--fake-speed`, `--no-rework`; `--help` prints them. Bad input exits with code 2 and usage.

### Dev phases

The default configuration in [`repositories.ts`](workflows/dev/repositories.ts)
uses the same phases as the [eShop dev workflow](https://github.com/antshc/eShop/tree/main/tools/loop/workflows/dev):

| Order | Phase | Behavior |
|---|---|---|
| 1 | Prepare repository | Resolve the target/base branch and fetch the repository |
| 2 | Plan and create Tickets | When the Spec has no sub-issues, run the read-only planner, validate its plan, then create sub-issues in dependency order |
| 3 | Deliver Tickets | Run the developer for each actionable Ticket, validate its one commit, retry rejected attempts, and close accepted Tickets |
| 4 | Publish | Push accepted work and create/update the draft PR after each Ticket |
| 5 | Verify application | Discover project verification skills, drive the integrated app, save evidence, and commit valid fixes |
| 6 | Refine changes | Capture the PR diff, run cleanup, and commit/push its changes; discard failed cleanup |
| 7 | Close worktree | Finish implementation before invoking the reviewer |
| 8 | Review pull request | Review the final head from the checkout through the project’s `code-review` skill |
| 9 | Check readiness | Mark ready only when delivery, verification, review axes, held Tickets, and the current head permit it |

These phases run as the normal dev workflow. The default configuration supplies all
delegates; a custom `RepositoryWorkflow` can omit an individual delegate to
skip it. A Spec with existing sub-issues, including closed or held ones, is never
planned again. App verification is skipped when no verification skill is installed.

Loop retains its developer commit contract: the developer makes one tagged commit
per Ticket. Verifier and cleanup agents leave edits uncommitted; the harness commits
accepted edits as separate `fix(...)` or `chore(...)` commits. Planner and reviewer
steps must leave the target checkout unchanged.

Install the project's app-verification skill and `code-review` skill in the target
repository before using those agents. The reviewer reports all four axes (`bugs`,
`quality`, `smells`, `initiative`) and the reviewed head. Missing/incomplete review,
blocking bug/initiative findings, changed head, held Tickets, or an unproven app
verdict keep the PR a draft and fail the Spec result. Cleanup is best effort.
The workflow never merges the PR or closes the Spec.

Console output contains only high-level phase transitions and outcomes. Commit
validation, restoration, individual gates, agent output, and command details stay
in execution history/telemetry or the DEBUG file. Add a terminal table independently
(the renderer is built in; there is nothing extra to install):

```sh
bun workflows/dev/main.ts --spec 42 --visualize
bun workflows/dev/main.ts --dry --fake-speed 0 --no-otel --visualize
```

`--spec NUMBER` selects one open Spec. `--visualize` enables the built-in phase table.
`--no-otel` disables export even when `--dry` selected an endpoint. Existing
`--otlp-endpoint`, fake seams, retry settings, and log-directory options remain available.

Phase declarations and actor names live in
[`definition.ts`](workflows/dev/definition.ts), built on the optional public
`WorkflowBuilder`. Execution stays in the dev-owned TypeScript helpers.

### Debugging with fakes

`--fake <seam>` (repeatable) replaces one external dependency of `dev` with a fake and leaves the
rest real; `--dry` fakes them all and publishes the run to the dashboard (OTLP endpoint
`OTEL_EXPORTER_OTLP_ENDPOINT`, default `http://localhost:4318`).

| Seam | Fakes | Needs |
|------|-------|-------|
| `agent` | the Copilot CLI: a scripted agent that reports usage and tool calls and commits in the worktree | none |
| `git` | the Workflow's Git reads and pushes, and the worktree (a temporary directory) | `agent` |
| `github` | `gh`: one canned Spec with two Tickets; writes are logged, not sent | none |
| `store` | the failure log, in a temporary directory | none |

```sh
bun workflows/dev/main.ts --dry                                  # everything fake, traces in the dashboard
bun workflows/dev/main.ts --fake agent --fake github --fake store   # real Git: the fake agent makes real empty commits and `dev` really pushes
bun workflows/dev/main.ts --dry --fake-speed 0 --no-rework        # instant, no failing first attempt
```

By default the second Ticket fails its first attempt so the retry path shows up in the trace.
In code, `Fakes` in [`dry.ts`](workflows/dev/dry.ts) builds the same fakes, and the library offers
`fakeAgent`, `FakeCliRunner` and `DirectoryGit` to run any functional slice in isolation.
Telemetry export needs the optional OpenTelemetry packages (see [Telemetry](#telemetry-optional)).

### Different agent workflows per repository

The example [`dev`](workflows/dev/) reads Specs and Tickets from `HARNESS`.
By default it implements only Specs targeting the harness itself. Pass
`repositories` to `main` to route Specs by their `repo:target:<owner/repo>` label to
explicitly configured checkouts under the harness's `workspace` folder.
Repositories must already be cloned; unknown targets are handed to a human.

Each `RepositoryWorkflow` pairs a `Repository` with a developer function (`runAgents`) accepting
`(TicketContext, AgentWorkspace)` and resolving to a `TicketResult`. The function
owns agent profiles, step ordering, prompt rendering, and response decoding. Optional
`runPlan`, `runVerify`, `runChore`, and `runReview` delegates configure the surrounding
phases; the default configuration supplies all four. A custom instance with only the
repository and developer function runs implementation only and leaves the PR a draft.
For example, one repository can implement only while another implements and reviews:

```ts
import { join } from 'node:path';
import { type AgentWorkspace, AgentProfile, AgentRequest, copilot } from 'loop';
import { implementTicket, loadPrompt } from './dev/agent.ts';
import { main } from './dev/main.ts';
import { type TicketContext, type TicketResult, RepositoryId } from './dev/records.ts';
import { DevResponse } from './dev/responses.ts';

const workspace = join(process.cwd(), 'workspace');
const template = loadPrompt('implement');
const implement = (context: TicketContext, worktree: AgentWorkspace): Promise<TicketResult> =>
  implementTicket(context, worktree, template);
const reviewer = new AgentProfile(copilot); // Set a model and reasoning effort here if desired.

/** Implements the Ticket, then reviews and optionally amends its one commit. */
async function implementAndReview(context: TicketContext, worktree: AgentWorkspace): Promise<TicketResult> {
  const result = await implement(context, worktree);
  if (result.status === 'failed') {
    return result;
  }
  const prompt = `Review commit ${result.commit ?? ''} against this Ticket:
${context.ticket.title}
${context.ticket.body}

Work only in this worktree. Verify the implementation; fix defects by amending
the existing commit, not by adding another. Keep the commit subject prefix
${JSON.stringify(context.identifier.toSubject())}. Leave the worktree clean.
Do not push, create PRs, or update Tickets.

Return a JSON object with identifier "${context.identifier.toString()}" and status
"completed", with result containing commit (the final full HEAD SHA), summary,
and verification. If review cannot complete, return status "failed" with
result containing reason.
`;
  const { output } = await worktree.agent(reviewer).run(new AgentRequest(prompt));
  return new DevResponse(context.identifier).decode(output);
}

const repository = (name: string): { checkout: string; ownerName: RepositoryId; worktrees: string } => ({
  checkout: join(workspace, name),
  ownerName: new RepositoryId('acme', name),
  worktrees: join(workspace, `${name}.worktrees`),
});

if (import.meta.main) {
  process.exitCode = await main(process.argv.slice(2), {
    repositories: [
      { repository: repository('api'), runAgents: implement },
      { repository: repository('web'), runAgents: implementAndReview },
    ],
  });
}
```

Run this user-owned script from the harness root. Configure `HARNESS` in
[`repositories.ts`](workflows/dev/repositories.ts) for the repository holding your Specs and Tickets.
The supplied list replaces the default target; include the harness explicitly
if it should also receive code changes. Repository identities are matched
case-insensitively; duplicate configurations fail the run.

The dev-owned [`deliverTickets`](workflows/dev/attempt.ts) function handles Ticket
iteration, refreshed Initiative history, retries, rollback, failure counts, and
Ticket closure. A delegate can return a failed `TicketResult` or throw
`TicketWorkflowError` for an expected, retryable failure; agent process failures
are also retried. Each retry invokes the whole delegate.

After the delegate finishes, the ticket workflow requires exactly one new,
correctly tagged commit, a clean worktree, and a reported SHA equal to HEAD.
Existing Initiative commits provide context; a matching subject alone does not
automatically close or skip a Ticket. Git operations and draft PRs use the target
repository; Ticket updates and failure-record identities remain on the harness.
Without session-enabled builders, agent steps start fresh sessions.

### Dev folder and named prompts

All dev-owned code, prompts, tests, and fakes live in [`workflows/dev/`](workflows/dev/).
It depends on the public Loop entry point, not sibling Workflow helpers.
The independent [`plan_implement.ts`](workflows/plan_implement.ts) is a self-contained,
function-based script: prompt text, response parsing, Git reads, and orchestration
live together, with no sibling Workflow imports.
There is no shared helper layer under `workflows/`; each Workflow owns the helpers it uses.

- [`main.ts`](workflows/dev/main.ts) and [`run.ts`](workflows/dev/run.ts): the entry point, high-level logging, phase reporting, retry settings, Loop hooks, and the dependency composition root ([`composition.ts`](workflows/dev/composition.ts), [`dependencies.ts`](workflows/dev/dependencies.ts), [`tokens.ts`](workflows/dev/tokens.ts)).
- [`definition.ts`](workflows/dev/definition.ts): named phases, actor ownership, visibility, and optional terminal/OTLP reporting.
- [`plan.ts`](workflows/dev/plan.ts), [`verify.ts`](workflows/dev/verify.ts), [`chore.ts`](workflows/dev/chore.ts), and [`review.ts`](workflows/dev/review.ts): planning, integrated app verification, cleanup, review, and readiness checks.
- [`repositories.ts`](workflows/dev/repositories.ts): `HARNESS` and `configuredRepositories`; edit its returned array to configure several repositories for `bun workflows/dev/main.ts`, or pass `repositories` to `main`.
- [`backlog.ts`](workflows/dev/backlog.ts) and [`attempt.ts`](workflows/dev/attempt.ts): Spec routing/publication and Ticket delivery.
- [`agent.ts`](workflows/dev/agent.ts), [`responses.ts`](workflows/dev/responses.ts), and [`journal.ts`](workflows/dev/journal.ts): prompt handling, response interpretation, and failure history.
- [`records.ts`](workflows/dev/records.ts): work items, repository identities, and outcomes.
- [`checkout.ts`](workflows/dev/checkout.ts), [`platforms.ts`](workflows/dev/platforms.ts), and [`commands.ts`](workflows/dev/commands.ts): Git, GitHub, and process access.
- [`prompts/`](workflows/dev/prompts/): named Markdown templates; `loadPrompt('implement')` reads `implement.md` relative to the folder, not the current directory.
- [`tests/`](workflows/dev/tests/): dev tests and local fakes; run `bun test workflows/dev/tests`.

Add templates to the prompt folder and let each repository's delegate select,
render, or combine them. For example, after adding `review.md`, use
`renderPrompt(loadPrompt('review'), { COMMIT: result.commit })` with that template's
arguments. Delegates own step ordering and agent profiles; there is no prompt
pipeline or configuration-file format. A missing template throws an error rather
than falling back to a different prompt.

## Optional Workflow Builder

`WorkflowBuilder` adds phase tracking, high-level console logs, terminal visualization,
and optional OTLP spans to ordinary TypeScript. It does not schedule
work: your script owns loops, conditions, retries, Git, and agent calls. Existing
`Agent()` workflows need no changes.

Import from the public entry point:

```ts
import { Kind, Phase, Phases, Role, Workflow, WorkflowBuilder, WorkflowRun } from 'loop';
```

### Run the fake e-commerce example

[`workflows/workflow_reporting.ts`](workflows/workflow_reporting.ts) is self-contained,
uses fake basket data, and makes no agent, Git, or GitHub calls. Run from the repository root:

```sh
bun workflows/workflow_reporting.ts
bun workflows/workflow_reporting.ts --fail-verification  # exits 1; PR stays a draft
bun workflows/workflow_reporting.ts --visualize
```

The example deliberately rejects the first implementation attempt and recovers on
its second attempt. It publishes a fake draft PR before verification, skips cleanup,
and marks readiness only after verification and review complete.

### Define phases; execute with ordinary TypeScript

This smaller example demonstrates the same interface:

```ts
import { Kind, Role, WorkflowBuilder, configureLogging } from 'loop';

configureLogging(); // The application owns logging.
const workflow = new WorkflowBuilder('eShop development')
  .phase('prepare', 'Prepare repository', Kind.CODE, Role.CODE)
  .phase('implement', 'Implement ticket', Kind.AGENT, Role.DEVELOPER)
  .phase('verify', 'Verify application', Kind.CODE, 'verifier')
  .phase('review', 'Review pull request', Kind.AGENT, Role.REVIEWER)
  .console()
  .build();

const basket: string[] = [];
const finished = await workflow.run({ spec: 42 }, async (run) => {
  await run.phase('prepare', () => {
    basket.length = 0;
  });

  for (const [ticket, product] of [
    [43, 'coffee'],
    [44, 'tea'],
  ] as const) {
    let recovered = false;
    for (let attempt = 1; attempt <= 2 && !recovered; attempt++) {
      recovered = await run.phase('implement', { attributes: { attempt, ticket } }, (phase) => {
        const changed = !(ticket === 43 && attempt === 1);
        if (phase.gate('product added', changed, 'No changes produced')) {
          basket.push(product);
        }
        return changed;
      });
    }
    if (!recovered) {
      run.fail(`Ticket #${String(ticket)} exhausted its retries`);
      return run;
    }
  }

  const verified = await run.phase('verify', (phase) => {
    const ok = basket.join() === 'coffee,tea';
    phase.set({ verdict: ok ? 'pass' : 'fail' });
    phase.gate('basket contains products', ok, 'Basket verification failed');
    return ok;
  });
  if (verified) {
    await run.phase('review', (phase) => {
      phase.set({ blocking_findings: 0 });
    });
    run.set({ ready: true });
  } else {
    await run.skip('review', 'Verification failed');
    run.set({ ready: false });
    run.fail('Verification failed; PR remains a draft');
  }
  return run;
});

console.assert(finished.outcome.status === 'completed');
console.assert(finished.outcome.attributes.ready === true);
```

`build()` snapshots the declarations. Each `workflow.run()` creates independent state;
reuse the definition for several Specs. Phase names must be unique and declared before
use; opening an undeclared phase throws `ValueError`. Definition order controls the pending
display, not execution order. Phases are callback scopes: `await run.phase(name, body)`
runs the body and resolves to its result, and the run is closed when its body settles, so
open every phase before leaving it. Nesting follows the async call chain, so a phase
opened inside another phase's callback records it as its parent.

### Outcomes and gates

- `run.phase(...)` starts a new occurrence every time, even for the same Ticket.
  Each occurrence has its own ID, parent ID, attempt attributes, and duration.
- Nested phases inherit their parent's attributes; inner changes do not mutate the parent.
- `phase.gate(check, ok, note)` records a claim and returns `ok`. A failed gate makes
  that phase failed. The caller decides whether to stop, retry, or continue.
- `phase.fail(reason)` is sticky: a clean return and `phase.succeed()` cannot erase failure.
- A body that returns completes a running phase. A thrown error marks it failed and propagates unchanged;
  SIGINT or SIGTERM cancellation marks it cancelled. Console errors summarize error names.
- `run.skip(name, reason, attributes)` records a skipped occurrence without running work.
  A declared phase never entered remains pending; it is not automatically called or skipped.
- A failed child does not automatically fail its parent: retries may recover. Call
  `run.fail(reason)` when recovery fails or a business outcome blocks delivery.
- `phase.set(...)` and `run.set(...)` record business facts; they do not infer success.
  Set verification verdict and PR readiness explicitly. Read the root result through
  `run.outcome` and occurrence history through `run.executions` after exit.

### Independent reporting options

| Option | Behavior | Dependency |
|---|---|---|
| No options | Track phases and gates without output | None |
| `.console()` | High-level transitions and final results through the Workflow logger | None |
| `.visualize({ stream })` | Live terminal table (Workflow name, Phase, Owner, Status, Time); pending phases visible; redrawn in place on a terminal, one final table when redirected | None (built in) |
| `.otel(observer)` | Export nested phase spans through an existing observer | Optional OpenTelemetry packages |

Combine the options as needed. Importing Loop never loads OpenTelemetry. Requesting OTLP
export without the packages fails with `TelemetryUnavailableError` and an install message.
`configureLogging()` or the host's logger controls console records;
the builder installs no handlers. Visualization defaults to stderr.
With console and visualization together, each
log transition is emitted once; the table is a separate view of the same phases.
Reporting errors are summarized without replacing the workflow's outcome or original error.

Use `.phase(..., { visible: false })` for details such as commit or restore operations.
They stay in execution history and telemetry but do not appear in console or terminal.
Console output excludes prompts, agent output, tool calls, command output, gate details,
and stack traces. Pass short, high-level reasons to `phase.fail()` and `run.fail()`.

Example console transitions (timestamps/logger omitted):

```text
INFO  eShop development · spec #42 started
INFO  Implement ticket · spec #42 · ticket #43 · attempt 1 started
WARN  Implement ticket · spec #42 · ticket #43 · attempt 1 failed · 0.0s · No changes produced
INFO  Implement ticket · spec #42 · ticket #43 · attempt 2 started
INFO  Implement ticket · spec #42 · ticket #43 · attempt 2 completed · 0.0s
INFO  eShop development · spec #42 completed · 0.0s · ready=True
```

### Share telemetry with agent observation

```ts
import { Agent, WorkflowBuilder, otlpObserver } from 'loop';

const observer = await otlpObserver({ endpoint: 'http://localhost:4318', resourcePerAgent: true });
const agentBuilder = Agent().withObserver(observer);
const workflowBuilder = new WorkflowBuilder('delivery').otel(observer);
```

Declare phases on `workflowBuilder`, build it, and open each phase around the existing
agent/worktree calls. `.otel(observer)` does not attach an agent observer automatically;
`withObserver(observer)` remains the independent agent opt-in. Share the same observer
so spans share providers and trace context. An active workflow phase parents an Agent Run,
including within a worktree; the worktree remains the fallback parent without a current span.
Gates and explicit failed outcomes are exported even when the body returns normally.

### Adapt an eShop-style dev workflow

Declare phases in a user-owned `definition.ts`, build once in `run.ts`, and pass the
active `run` to helpers accepting a phase tracker. The
[eShop reference](https://github.com/antshc/eShop/tree/main/tools/loop/workflows/dev)
adds optional planning/ticket creation before delivery, publishes tickets as they land,
verifies the integrated app, performs optional cleanup, closes the implementation worktree,
then reviews the PR and checks readiness. Preserve that ordering in your code. The builder
adds no GitHub writes, commits, Ticket policy, or review logic.

The actual [`dev` Workflow](#dev-phases) uses the builder for the default phase pipeline.
The smaller fake example demonstrates the library independently. Existing workflow-owned
spans also remain supported.

### Track phases without a builder

```ts
import { Kind, Phases, Role } from 'loop';

const phases = new Phases();
await phases.phase('verify', { kind: Kind.CODE, owner: Role.CODE }, (phase) => {
  phase.gate('basket has coffee', ['coffee'].includes('coffee'));
});
```

Direct `Phases` has no registry and emits no output. The builder and phase tracking
are both optional for agent execution.

## Logging

The library logs through named loggers and installs no output sinks.
Use your application's logging or call `configureLogging`:

```ts
import { configureLogging } from 'loop';

configureLogging();                                                // INFO: console only
configureLogging({ level: 'debug' });                              // DEBUG: console + JSON lines in ./loop.log
configureLogging({ level: 'debug', logFile: 'logs/run.log' });     // custom file
configureLogging({ level: 'debug', logFile: null });               // DEBUG without a file
```

It configures only the `loop` logger subtree; pass `logger: ''` to configure every logger of your application.
Records are `{"message", ...extras}` with no level, time, or name fields in the file.

`dev` writes its file to `<log-dir>/dev.log`, and only with `--log-level DEBUG`.

## Telemetry (optional)

Install the optional OpenTelemetry packages (`bun add @opentelemetry/api @opentelemetry/sdk-trace-base
@opentelemetry/sdk-metrics @opentelemetry/resources @opentelemetry/exporter-trace-otlp-http
@opentelemetry/exporter-metrics-otlp-http`; this repository already has them as development dependencies),
start an OTLP backend such as the standalone
[Aspire dashboard](https://aspire.dev/dashboard/standalone/) (UI `:18888`, OTLP/HTTP `:4318`;
see [`docker-compose.yml`](docs/prototypes/dashboard-aspire/docker-compose.yml)), and opt in on the builder:

```ts
import { Agent, AgentRequest, DEFAULT, Role, otlpObserver } from 'loop';

const observer = await otlpObserver({ endpoint: 'http://localhost:4318', serviceName: 'my-workflow' });
await Agent()
  .withGit(options)
  .withObserver(observer)
  .open(async (worktree) => {
    const span = observer.span('#1 add basket', { 'loop.ticket.number': 1 }, Role.CODE); // your own span; runs nest under it
    try {
      await span.run(() => worktree.agent(DEFAULT).run(new AgentRequest('...')));
    } finally {
      span.end({ attributes: {}, error: null, gates: [] });
    }
  });
await observer.shutdown();
```

Each Agent Run becomes an `invoke_agent` span with `execute_tool` children, token, AIU and
premium-request usage (read from the Copilot CLI's session log), and the Worktree a
`loop.worktree` span; metrics go to the same backend. Prompts, outputs and tool arguments are
exported only with `otlpObserver({ captureContent: true })`. Without `withObserver` nothing changes.
A span appears in the dashboard when it ends, and Codex Agent Runs carry no usage. Pass any
object with `agentRun(record)` and `worktree(event)` methods to `withObserver` to send the
records elsewhere. Without the packages, `otlpObserver()` rejects with `TelemetryUnavailableError`.
Flush and shutdown failures are logged and never fail the run. The packages are not officially supported on Bun,
and export has been verified against in-memory exporters, not a real collector.
The dev Workflow opts in with `bun workflows/dev/main.ts --otlp-endpoint http://localhost:4318`.
Name a profile from the shared `Role` vocabulary (`DEFAULT.with({ name: Role.REVIEWER })`) and pass `otlpObserver({ resourcePerAgent: true })` to export each named agent as its own resource, which Aspire colors apart:

| `Role` | Exports |
|--------|---------|
| `ENGINEER` | the request that starts a run (`observer.span('request', {}, Role.ENGINEER)`) |
| `CODE` | the Worktree and deterministic steps: checks, validation, Git, GitHub (`observer.span(..., Role.CODE)`) |
| `PLANNER`, `DEVELOPER`, `REVIEWER` | the agents of those names (`new AgentProfile(cli, { name })`) |

`dev` uses `ENGINEER` for each Spec, `CODE` for deterministic phases, and separate planner, developer, verifier, chore, and reviewer actors for agent phases; `plan_implement` names its agents `PLANNER` and `DEVELOPER`.
See [Run Observation](docs/concepts/str-run-observation.md).

## Library examples

The snippets in this section build on one another. They use
`{ dryRun: true }` to log Git and agent operations without executing
them. Remove that option only when ready to run real Git operations and agents.

One-shot run: one git lifecycle per `run()`.

```ts
import { Agent, AgentRequest, GitOptions, MergeToHeadStrategy, configureLogging } from 'loop';

configureLogging();
const dry = { dryRun: true };
const root = 'workspace/repo1.worktrees';
const repo = 'workspace/repo1';
await Agent(dry)
  .withGit(new GitOptions({ repositoryPath: repo, rootPath: root, strategy: new MergeToHeadStrategy() }))
  .create()
  .run(new AgentRequest('Implement ticket #123 in repo1'));
```

Role profiles; which CLI and model fill a role is a Workflow choice.

```ts
import { AgentProfile, CodexCli, copilot } from 'loop';

const PLANNER = new AgentProfile(copilot, { model: 'claude-opus-4.5', reasoningEffort: 'high' });
const DEVELOPER = new AgentProfile(new CodexCli(undefined, { hookTrustBypass: true }), { model: 'gpt-5-codex', reasoningEffort: 'high' });
const REVIEWER = new AgentProfile(copilot, { model: 'claude-sonnet-4.5' });
```

Shared worktree: one git lifecycle, several CLIs, one named session kept per CLI.

```ts
import {
  BranchStrategy,
  SessionEndAgentCliHook,
  SessionName,
  SessionStartAgentCliHook,
  WorktreeReadyLoopHook,
} from 'loop';

const loopHooks = [
  new WorktreeReadyLoopHook('cp "$LOOP_REPOSITORY/.env" .env'),
  new WorktreeReadyLoopHook('bun install', 600),
];
const shared = Agent(dry)
  .withGit(new GitOptions({ repositoryPath: repo, rootPath: root, strategy: new BranchStrategy('loop/ticket-123', 'main'), loopHooks }))
  .withSession();
shared.withAgentCliHooks(
  new SessionStartAgentCliHook('./scripts/session-start.sh'),
  new SessionEndAgentCliHook('notify-send', 3),
);
await shared.open(new SessionName('ticket-123'), async (wt) => {
  const plan = await wt.agent(PLANNER).run(new AgentRequest('Plan ticket #123 in repo1'));
  const developer = wt.agent(DEVELOPER);
  await developer.run(new AgentRequest(`Implement this plan:\n${plan.output}`));
  await developer.run(new AgentRequest('Fix failing tests')); // resumes the Codex session
  // A fresh session gives an unbiased review.
  await wt.agent(REVIEWER, SessionName.new()).run(new AgentRequest('Review the diff against the plan'));
});
```

Without `withSession()`, each run starts under a new `loop-<hex>` name.

```ts
const fresh = Agent(dry)
  .withGit(new GitOptions({ repositoryPath: repo, rootPath: root, strategy: new BranchStrategy('loop/ticket-123', 'main') }))
  .create();
await fresh.run(new AgentRequest('Plan ticket #123 in repo1'));
await fresh.run(new AgentRequest('Implement ticket #123 in repo1'));
```

By default the CLI starts inside the worktree. To start it in the harness checkout
instead (so it loads the harness context), set `startMode` and `harnessDir`; Loop
grants the worktree with `--add-dir` and prefixes each prompt with a `cd` instruction:

```ts
import { Agent, AgentStartMode } from 'loop';

Agent({ harnessDir: '/path/to/harness', startMode: AgentStartMode.HARNESS });
```

See [`workflows/dev/`](workflows/dev/) for a complete Workflow,
[`workflows/plan_implement.ts`](workflows/plan_implement.ts) for two runs with
different models on one worktree, and
[`workflows/dev/prompts/implement.md`](workflows/dev/prompts/implement.md) for
the default `dev` prompt template.

## Worktree recovery and PR maintenance

Loop's Git service manages worktree lifecycle only. It never stages, commits, or
pushes changes. The Workflow or agent must explicitly commit before a successful
worktree exit; `GitOptions` has no commit message option.

- Clean success or failure removes the directory and retains named-branch commits.
- Dirty failure preserves the directory and propagates the original error.
- A successful body leaving edits rejects with `WorktreeError` and preserves the directory.
- Opening the same branch/path validates repository ownership and branch identity,
  then reuses preserved work. Existing local commits are never silently reset.
- An explicit `new BranchStrategy(branch, baseBranch)` starts a new local branch from
  that exact ref. Without it, an existing origin branch is preferred over HEAD.
- Successful `MergeToHeadStrategy` runs fast-forward the host and delete the temporary
  branch. Failed/divergent merges retain that branch for Workflow-owned recovery.
- Stale metadata is pruned. Empty orphan directories or lone broken Git links can be
  removed; nonempty unrecognized directories are preserved and reported.
- The Workflow must serialize users of the same named worktree. No library lock is
  implied. Relative roots remain inside cwd; absolute roots are explicit locations.

Worktree observers emit `retained` when a directory remains, instead of claiming it
was removed. OTLP closes the run's span and marks retention independently of disk cleanup.
Loop has no isolated sandbox synchronization backend; patch-based sync recovery is
not applicable to its host worktrees or Docker context adapter.

## Interruption

SIGINT (Ctrl-C) and SIGTERM end the agent's whole process group (SIGTERM, then SIGKILL after a grace period),
mark the active phase `cancelled`, run worktree cleanup, and exit with code 130 or 143. A second signal
force-kills the remaining groups and exits at once. Entry points get this by running `main` through `runEntrypoint`.
Verified on Linux; macOS and Windows behavior is untested.

See [PR maintenance](workflows/pr_babysit/README.md) for continuous PR discovery,
Address/Maintainer profiles, JSON recovery, and workflow-owned commit/push operations.

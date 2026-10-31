# Loop Glossary

Shared domain language for the [Loop library](README.md) and the [Agentic Software Factory](docs/researches/unified-event-driven-sdlc-workflow.md). The library supplies execution primitives; a factory composes them with domain policy. Terms describe concepts, not necessarily implemented features. **Proposed** identifies factory capabilities that remain design targets.

## Loop Library

Reusable building blocks in the installable `loop` package, including the Workflow runtime, the typed agent contract and the shared Git and process helpers. Loop does not ship a CLI, a fixed SDLC, or built-in workflows.

### Composition and execution

| Term | Definition |
| --- | --- |
| **Loop** | Python library for composing headless coding-agent execution, Git worktrees, sessions, hooks, and observation. **Avoid:** factory, CLI, workflow engine. |
| **Workflow** | User-owned Python procedure, written as a declarative Workflow definition on the public `loop` API and run by Loop's Workflow runtime, that owns its Stages, policy, interpretation of results, and external effects. Examples: `implement_spec`, `plan_implement`, `pr_babysit`, `address_once`. **Avoid:** built-in workflow, plugin, fixed pipeline. |
| **Workflow runtime** | Loop's generic interpreter of Workflow definitions: Stage attempts, gates, repository contracts, routes, the Execution ledger and run, scope and Stage attempt spans. It knows no GitHub, Ticket or pull request. **Avoid:** factory runtime (former name), engine. |
| **Typed agent** | `TypedAgent`: one reusable role over an `AgentProfile` that encodes a typed request into a prompt and decodes the output into a typed reply or a declined value. Loop owns the contract; concrete role agents belong to a Workflow. **Avoid:** agent role (the responsibility), agent client (the CLI boundary). |
| **Workflow run** | One run of a Workflow definition for one work item, ending completed, idle, held, handed off, failed or interrupted. Distinct from an Agent run. |
| **Phase** | Retired: the former Phase-reporting API (`WorkflowBuilder`, `Phases`, `Phase`, `Kind`) was removed; use Stage attempt span. **Avoid.** |
| **Agent builder** | `Agent()` / `AgentBuilder`: composes CLI, Git, Docker, session, hook, and observer options and creates clients or a shared agent workspace. |
| **Agent client** | `AgentClient` accepting Agent requests for one profile, optionally bound to a worktree. Host execution uses host permissions; a worktree is not a sandbox. **Avoid:** agent role, agent runner, sandbox. |
| **Agent profile** | Reusable choice of CLI, model, reasoning effort, instructions, tool permissions, paths, environment, timeout, and arguments, defined in code. **Avoid:** Agent options, factory role. |
| **Agent CLI** | External headless agent program (for example Copilot CLI or Codex CLI) invoked through a Loop adapter. |
| **CLI runner** | Process boundary used by Loop to invoke a configured Agent CLI and capture its result. **Avoid:** factory orchestrator. |
| **Agent request** | `AgentRequest`: one prompt submitted to an Agent client; starts or resumes a session. **Avoid:** ticket, workflow run. |
| **Agent result** | `AgentResult`: raw CLI output, session identity, and process exit code. It is not validated proof of task completion. **Avoid:** verification verdict. |
| **Agent run** | One execution of an Agent request (one observed turn). Distinct from a long-lived Agent session and a multi-Stage Workflow run. |
| **Agent session** | Agent conversation context identified separately from an OS process; may span multiple Agent runs. **Avoid:** process, container. |
| **Session store** | Maps logical session names to native CLI session handles for continuation; the library supplies an in-memory implementation. |
| **Session lifecycle** | Start, remember, and resume an Agent session when enabled. **Avoid:** process lifecycle. |
| **Stateless execution** | An Agent request without a resumable session store; every run creates a fresh session. **Avoid:** one-shot workflow. |

### Repository, isolation, and observability

| Term | Definition |
| --- | --- |
| **Harness** | Repository from which a Workflow runs and where its instructions, specs, and skills live. It can also contain source code. **Avoid:** control repo, wrapper repo. |
| **Single Repo** | Harness layout where harness and source code share one repository. **Avoid:** monorepo, standalone harness. |
| **Multi Repo** | Harness layout where a separate parent harness wraps multiple source repositories. **Avoid:** wrapping harness, parent harness. |
| **Codebase Checkout** | Clone of a target repository whose changes occur in worktrees: the harness itself in Single Repo, or a wrapped clone in Multi Repo. **Avoid:** working copy, source clone. |
| **Workspace Folder** | Harness workspace containing wrapped clones and worktrees. The exact path is caller-configurable. **Avoid:** repos folder. |
| **Git strategy** | Rule for selecting how a checkout is used: current HEAD, named branch worktree, or temporary branch merged into HEAD. |
| **Worktree** | Separate Git working directory used by agents on a branch. Dirty worktrees are retained for recovery; worktrees do not provide a security boundary. **Avoid:** sandbox. |
| **Run lifecycle** | Open worktree, execute applicable Loop hooks and agent runs, then clean up or retain the worktree and optionally merge the temporary branch. Commit and push remain explicit actions. **Avoid:** sandbox lifecycle. |
| **Loop hook** | Host shell command registered for a Loop lifecycle point; failure can fail the run. **Avoid:** unqualified hook, Agent CLI hook. |
| **Loop hook point** | `worktree-ready`, `worktree-removing`, or `run-finished`. **Avoid:** event, trigger, workflow stage. |
| **Agent CLI hook** | Native CLI lifecycle command configured through Loop; observe-only in Loop's integration and does not steer a run. **Avoid:** Loop hook, event handler. |
| **Agent CLI hook point** | CLI-specific event such as session start or agent stop, distinct from Loop's worktree-level hook points. |
| **Run observer** | Optional `RunObserver` receiving Agent run records and worktree events, including usage, tool calls, and timings. Observation must not change execution. **Avoid:** telemetry hook, gate. |
| **Dry run** | Execution mode using logging or fake adapters to inspect Git and agent operations without live changes. It does not prove acceptance criteria. |

## Agentic Software Factory

Product and SDLC language for user-owned workflows built **on** Loop. Warp Factories and Super Simple Software Factory (SSSF) inform these definitions; neither product's conventions are automatically Loop library features.

### System and work

| Term | Definition |
| --- | --- |
| **Agentic Software Factory (ASF)** | Delivery system coordinating human requests, specialized agents, deterministic actions, evidence, and policy gates across the SDLC. **Avoid:** synonym for Loop library. |
| **Factory definition** | Versioned configuration of a factory's agents, workflows, integrations, environments, triggers, and policies. Shape is implementation-specific. |
| **Factory workflow** | Domain procedure that determines which tasks run, in what order, and under what policy; implemented as a Workflow (in the factory, a Workflow definition), not a built-in Loop pipeline. |
| **Work item** | Stable identity for one incoming unit of work through intake, decisions, execution, review, and handoff. It may originate as an issue, PR, or direct request. **Avoid:** equating it with one agent run. |
| **Intake** | Accept and normalize a work item from an external source; establish its identity and context before routing. |
| **Trigger** | Condition starting or resuming work, such as an issue event, PR update, user command, schedule, or monitor finding. |
| **Event** | Immutable statement that something happened, such as `PullRequestUpdated`. In the factory, a Stage's typed pass payload recorded in the Execution ledger and read by later Stages. **Avoid:** command. |
| **Command** | Request for an action handled by one responsible component, such as `ImplementTicket`. **Avoid:** event. |
| **Automation** | Trigger plus filters and target handler/agent, used to route matching events into work. Filters do not grant permissions. |
| **Workflow instance** | Persisted progress of one work item across events and pauses. **Proposed** for the unified event-driven factory; distinct from an in-process Loop Workflow run. |
| **Initiative** | Coordinated product change identified by an Initiative ID; may span target repositories and their Specs. **Avoid:** generic project, epic. |
| **Spec** | Planning Ticket containing expected behavior, scope, and acceptance criteria for an Initiative and target repository; can parent implementation Tickets. **Avoid:** implementation task, epic. |
| **Ticket** | Platform-neutral actionable task, such as a GitHub issue or Azure DevOps work item. In the proposed unified workflow, child Tickets implement their parent Spec. **Avoid:** generic issue when role matters. |
| **Ticket Tracker** | Domain-facing service for reading and updating Tickets, Specs, dependencies, comments, labels, and status through a provider such as GitHub Issues or Jira. The external tracker owns work state; it is not the factory execution ledger or checkpoint store. |
| **Dependency graph** | Relationships between Tickets that constrain execution order; an eligible, unblocked Ticket belongs to the **ready frontier**. |
| **Pull request (PR)** | Proposed code change and review surface with comments, review threads, commits, and check results. It is not itself a passing merge gate. |
| **Comment** | Conversation-level message on a Ticket, Spec, or pull request. A **review comment** is distinct: it is anchored to a diff location inside a review thread. **Avoid:** issue comment, timeline comment. |

### Actors, tools, and execution

| Term | Definition |
| --- | --- |
| **Orchestrator** | Actor that routes work, invokes specialized agents, handles results, and enforces workflow policy. **Ralph** is Loop's established name for an autonomous iteration driver; **Foreman** is Warp's name for its coordinating factory agent. Neither is a mandatory Loop library service. |
| **Crew** | Specialized agent team working toward a shared outcome. Historical Loop names include Codey, Chorey, and Testy. **Avoid:** using Crew to mean one agent or one CLI session. |
| **Agent role** | Responsibility within factory policy, independent of CLI/model/profile. One role may use different profiles; one profile may serve multiple tasks. |
| **Triage agent** | Investigates an intake request, determines scope, classifies work, and identifies blockers. |
| **Spec agent** | Drafts or revises specifications and ticket descriptions; proposed `/spec` requests changes on the commented issue. |
| **Developer agent** | Implements ready Tickets and code fixes delegated by review/CI handlers. Also called Implement agent in other factories. |
| **Reviewer agent** | Independently assesses changes against requirements, security, and quality criteria; reports findings, not merely a test exit code. |
| **Address agent** | Investigates PR feedback and recommends replies or fixes; the proposed unified workflow routes code changes back to Developer. |
| **Maintainer agent** | Diagnoses CI and PR readiness problems, distinguishes transient failures from defects, and requests remediation. |
| **Verifier** | Checks observable feature behavior and collects evidence against acceptance criteria. Distinct from a test command and a reviewer judgment. |
| **Monitor** | Detects actionable post-delivery failures or product signals and creates new work items. |
| **Skill** | Reusable procedural instructions, references, and scripts available to an agent. It informs behavior but is not a permission boundary. |
| **Tool (Agent Tool)** | Capability an agent invokes directly *during* an Agent run, such as shell, file editing, GitHub API, browser, or MCP. Tools are governed by permissions; exposing a tool does not make it a workflow decision. **Avoid:** Workflow Action. |
| **Workflow Action** | Operation invoked by deterministic Workflow code, before or after an Agent run or without any agent, such as updating a Ticket, creating a PR, or checking CI status. Workflow code decides whether and when it runs. **Avoid:** Agent Tool, agent prompt. |
| **Service / Adapter** | Reusable implementation behind an Agent Tool or Workflow Action. A service exposes domain operations (for example `TicketTracker`); an adapter connects those operations to GitHub, Jira, Git, or another external system. **Avoid:** equating the service with one tool call. |
| **Deterministic step** | Code-controlled Workflow step (an action or effect Stage) that may invoke one or more Workflow Actions, validate results, and record gates. **Avoid:** asking an agent to rediscover a fixed operation for every run. |
| **Execution environment** | Host, container, VM, or managed runner supplying repository access, tools, credentials, and compute to an agent. |
| **Sandbox** | Enforced execution or filesystem boundary. A Git worktree alone is **not** a sandbox. |

### Control flow and evidence

| Term | Definition |
| --- | --- |
| **Stage** | Reusable building block of a Workflow: one factory activity (such as plan, implement, review, or address feedback) that may run an agent or a deterministic action and a gate as a unit. Workflows compose Stages from a shared library; a Stage's contract is its typed input, the Event it produces, its gate and its Routes. **Avoid:** Phase. |
| **Workflow definition** | Declarative, immutable description of a factory Workflow: the Stages it places, the Routes between them and the constructs that order them (sequence, per-item loop, switch on a closed Event sum, workspace scope). It is validated as it is built and executed by Loop's Workflow runtime once per work item. **Avoid:** pipeline, DAG. |
| **Stage attempt** | One execution of a Stage for a work item, ending in pass, fail or hold. It is recorded in the Execution ledger and reported as one span; a rework is a further attempt of the same Stage. **Avoid:** Phase. |
| **Route** | Where control goes after a Stage ends in pass, fail or hold: on to the next step, to the next item, back for rework, to a terminal stop, to a hand-off to a person, or to a sibling case. |
| **Selector** | Pure function that offers the candidate items of a per-item loop in order; it is asked again after every item and has no effect of its own. |
| **Scope** | One bounded part of a factory run with its own status: the run itself, one item of a per-item loop, or one workspace. A scope that is handed off stays open across runs until a person releases it. |
| **Handoff** | Transfer of task context and artifacts to another agent, deterministic step, or human; the receiver must have sufficient durable context. |
| **Envelope** | Structured, schema-validated agent response containing claims, decisions, artifact references, and next-step recommendations; the Workflow checks it before acting. **Proposed** for unified factory messages; Loop's `AgentResult` remains raw CLI output. |
| **Execution ledger** | Append-only durable record, per work item, of the attempts, outcomes, effects, evidence, and recovery information from which an interrupted or repeated run resumes. Written only by the Workflow runtime; not a Loop session store and not the Ticket Tracker. |
| **Checkpoint** | Saved restart position after an externally visible effect or stage, used for idempotent resume. |
| **Evidence** | Inspectable proof of a claim: test report, screenshot, API response, logs, changed-file diff, or CI result. **Avoid:** relying on an agent's success statement alone. |
| **Gate** | Explicit predicate over evidence and policy with pass, fail, or hold outcome; it determines whether work may advance. **Avoid:** synonymous with a stage or reviewer. |
| **Automated gate** | Machine-checkable gate such as tests pass, artifacts exist, no prohibited files changed, or required CI checks succeeded. |
| **Human gate (HITL)** | Hold requiring a human decision, clarification, or risk acceptance. In the proposed Loop workflow, `hitl` blocks Ticket execution until removed; it is not applied to every generated Ticket. In `implement_spec`, removing `hitl` from a held Spec or Ticket releases it: the next run records the release in the Execution ledger and starts a fresh scope with a fresh rework budget. |
| **Merge gate** | Combined PR readiness rule over required checks, approvals, unresolved discussions, and repository policy. A green test alone is insufficient. |
| **Verification** | Establishes that actual behavior meets acceptance criteria using reproducible checks and evidence; distinct from reviewing code quality. |
| **Rework loop** | Bounded return to implementation or an earlier stage after gate failure or review feedback, followed by fresh validation. |
| **Fan-out / fan-in** | Run independent tasks concurrently / join their validated outcomes at an explicit dependency or decision point. |
| **Human handoff** | Present artifacts, findings, and evidence to the requester for a decision. Handoff does not itself imply merge or completion. |
| **Release** | Publish or deploy an accepted change under delivery policy, then verify its rollout. Distinct from PR creation. |
| **Evaluation (eval)** | Repeated assessment of agent/workflow outcomes against known criteria or scored cases, beyond a single work item's tests. |
| **Self-improvement** | Evidence-driven updates to prompts, skills, tools, routing, or workflow policy, validated through evals before adoption. |

### Sources and boundaries

- [Loop library building blocks](docs/building-blocks/loop.md), [public API](src/__init__.py), [Workflow runtime](src/runtime/__init__.py), and [unified workflow plan](docs/researches/unified-event-driven-sdlc-workflow.md).
- [Warp Factories overview](https://docs.warp.dev/factories/), [how factories work](https://docs.warp.dev/factories/how-factories-work/), [agents](https://docs.warp.dev/factories/factory-agents/), [automations](https://docs.warp.dev/factories/automations/), and [issue-to-PR example](https://github.com/warpdotdev/warp-factory-examples/tree/main/examples/02-sdlc-issue-to-pr).
- [SSSF overview](https://github.com/antshc/super-simple-software-factory/blob/main/.claude/skills/sssf/cookbooks/sssf_overview.md), [agent-driven workflows](https://github.com/antshc/super-simple-software-factory/blob/main/.claude/skills/sssf/cookbooks/create_adw.md), and [deterministic gates](https://github.com/antshc/super-simple-software-factory/blob/main/.claude/skills/sssf/templates/adws/adw_modules/gates.py).
- **Boundary:** Loop owns agent execution, local Git/worktree lifecycle, the Workflow runtime (with its Execution ledger and spans), the typed agent contract, and the shared Git and process helpers. The factory owns intake, routing, business gates, policy, role agents, Stages, the GitHub integration including Spec and Ticket models, GitHub publication decisions, approvals, and delivery. Proposed terms do not assert that the event-driven factory is already implemented.

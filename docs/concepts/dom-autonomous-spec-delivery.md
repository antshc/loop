# Autonomous Spec Delivery

## Purpose
Loop delivers the approved Tickets of every open Spec to a draft pull request without an operator in the session, one fresh agent run per Ticket, and escalates a Ticket to a human once it keeps failing.

## Definition
- **Actors:** Operator; Loop (the example `dev` Workflow); Copilot agent (one fresh headless run per Ticket); GitHub.
- **Business processes:** Run AFK Dev Service; Develop Spec; Deliver Ticket.
- **Starts:** Operator runs the `dev` Workflow script (through their own alias) for a repository board.
- **Ends:** Every open Spec not labelled `hitl` was attempted; each Develop Spec run ends with its validated commits pushed and its draft pull request ensured.

## Business Processes

### Run AFK Dev Service
Actor: Operator; Trigger: the `dev` Workflow script is run for a repository board; Action: Loop lists the open Specs, skips those labelled `hitl`, and runs Develop Spec for each remaining one; Outcome: every remaining Spec was attempted. Notes: there is no dry run and no Spec-level attempt cap; failures are counted per Ticket.

### Develop Spec
Actor: Loop; Trigger: a Spec not labelled `hitl`; Action: resolve the Initiative id from the Spec title prefix and the target repository and base branch, push the local feature branch and ensure the draft pull request when it is ahead of `origin`, create the worktree, then run Deliver Ticket for the first actionable Ticket until none remains or the Spec is labelled `hitl`; finally push and ensure the draft pull request; Outcome: all Tickets delivered — the pull request link is commented on the Spec, which stays open for the human merge — or the Spec stopped on `hitl` with its validated work pushed.

```mermaid
%%{init: {'themeVariables': {'lineColor': '#8b949e'}}}%%
%% diagram-id: develop-spec-swimlane
swimlane-beta TB
  accTitle: Develop Spec responsibility
  accDescr: Shows how Loop, the Copilot agent and GitHub share the work of delivering one Spec one Ticket at a time.

  subgraph loop [Loop - dev Workflow]
    start([Spec not labelled hitl])
    meta[1 - Resolve Initiative id, target repo, base branch]
    ahead{2 - Local feature branch ahead of origin?}
    pre[2a - Push and ensure draft PR]
    wt[3 - Create worktree]
    pick{4 - Actionable Ticket left?}
    deliver[[5 - Deliver Ticket]]
    stopped{6 - Spec labelled hitl?}
    publish[7 - Push and ensure draft PR]
    link[8 - Comment PR link on Spec]
    endNode([Spec run ended])
  end

  subgraph ext [External systems - GitHub]
    tickets[(Spec sub-issues)]
    remote[(Target repo remote and PR)]
    spec[(Spec issue)]
  end

  start --> meta --> ahead
  ahead -->|yes| pre --> wt
  ahead -->|no| wt
  pre -->|push, PR| remote
  wt --> pick
  pick -->|query actionable| tickets
  pick -->|yes, first| deliver --> stopped
  stopped -->|no| pick
  stopped -->|yes| publish
  pick -->|none| publish
  publish -->|push, PR| remote
  publish --> link -->|all delivered| spec
  link --> endNode

  classDef default fill:#242424,stroke:#8b949e,color:#c9d1d9,stroke-width:1px
```

### Deliver Ticket
Actor: Loop and one fresh Copilot agent run; Trigger: Develop Spec selects the first actionable Ticket; Action: Python records `previous_head`, renders the prompt with the Ticket, the Initiative's `ccode(<initiative-id>|` commits on the branch, the task id `<initiative-id>|<ticket-number>`, and the contract; the agent implements, verifies, makes one `ccode(<initiative-id>|<ticket-number>): <message>` commit, and returns the response envelope; Python validates the response and Git; Outcome: success closes the Ticket with the commit SHA, summary, and verification and resets its failure count; any failure resets the worktree to `previous_head` and counts once, and the second failure labels the Ticket and the Spec `hitl` with the reason commented on both. Notes: a failure is an agent-reported `failed`, a missing or invalid response, an `identifier` other than this Ticket's, HEAD unchanged, a non-matching subject, a dirty tree, a reported SHA other than HEAD, or a crashed or timed-out run.

```mermaid
%%{init: {'themeVariables': {'lineColor': '#8b949e'}}}%%
%% diagram-id: deliver-ticket-swimlane
swimlane-beta TB
  accTitle: Deliver Ticket responsibility
  accDescr: Shows how Loop and one fresh Copilot agent run share the work of delivering and validating one Ticket.

  subgraph loop [Loop - dev Workflow]
    start([First actionable Ticket])
    head[1 - Record previous HEAD]
    prompt[2 - Render prompt with Ticket, Initiative commits, task id, contract]
    parse[4 - Parse response envelope]
    valid{5 - Response and Git valid?}
    close[6 - Close Ticket with SHA, summary, verification]
    reset[7 - Reset worktree to previous HEAD]
    cap{8 - Second failure?}
    hitl[9 - Label Ticket and Spec hitl, comment reason]
    endNode([Back to Develop Spec])
  end

  subgraph agent [Copilot agent - fresh run]
    work[3 - Implement, verify, commit once, return response]
  end

  subgraph ext [External systems - GitHub]
    issues[(Ticket and Spec issues)]
  end

  start --> head --> prompt --> work --> parse --> valid
  valid -->|yes| close -->|close| issues
  valid -->|no| reset --> cap
  cap -->|no, retry same Ticket| endNode
  cap -->|yes| hitl -->|labels, comments| issues
  close --> endNode
  hitl --> endNode

  classDef default fill:#242424,stroke:#8b949e,color:#c9d1d9,stroke-width:1px
```

## Relationships

```mermaid
%%{init: {'themeVariables': {'lineColor': '#8b949e'}}}%%
%% diagram-id: autonomous-spec-delivery-relationships
flowchart LR
    run[["1 - Run AFK Dev Service"]]
    dev[["2 - Develop Spec"]]
    ticket[["3 - Deliver Ticket"]]
    pr(["Draft pull request"])
    hitl(["Ticket and Spec labelled hitl"])
    back(["Back to: Run AFK Dev Service"])

    run -- "Spec not hitl" --> dev
    dev -- "one fresh agent run per Ticket" --> ticket
    ticket -- "second failure" --> hitl
    dev --> pr
    hitl -. "Operator removes label" .-> back

    classDef default fill:#242424,stroke:#8b949e,color:#c9d1d9,stroke-width:1px
```

A solid edge is automatic; a dotted edge is a separately initiated step, labelled with its initiator.

Decisions: [ADR 0009](../adr/0009-run-one-fresh-agent-per-ticket-from-python-and-let-the-agent-commit-it.md) (per-Ticket run, task commit, Git validation), [ADR 0007](../adr/0007-stream-agent-output-live-and-parse-it-after-exit-with-a-per-agent-kind-output-parser.md) (response envelope and output parser), [ADR 0006](../adr/0006-keep-commit-push-pull-request-and-ticket-state-changes-in-python.md) (Python owns push, pull request, and Ticket state).

## Implementation Map
| Concern | Stable anchor | Semantic locator |
|---|---|---|
| External contract | Operator-run service for one repository board | `workflows/dev.py`: runnable script `main(argv)`, options `--harness-root`, `--log-dir`, `--log-level` |
| Spec and Ticket selection | Open Specs; actionable Tickets | `loop`: `GitHubClient.get_specs`, `GitHubClient.get_actionable_issues` |
| Execution | Fresh non-interactive Copilot run per Ticket | `loop`: `AgentClient`, `Sandbox` |
| Failure bound | Per-Ticket failure count across runs | `loop`: `ExecutionStore` |
| Tests | Workflow scenarios against fakes | `tests/test_workflows.py` |

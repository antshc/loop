# AI Agent Team
**Type:** Architecture Pattern

## Purpose

Define how Loop can execute work with either one general-purpose Headless AI Agent or a Crew of specialized agents coordinated toward the same outcome.

## Concept

An AI Agent Team is multiple specialized agents with distinct responsibilities coordinated toward a shared outcome. In this project, such a team is called a **Crew**.

Loop supports two agent configurations:

- **General-purpose agent** — the default. One Headless AI Agent receives the task and owns the full execution.
- **Crew** — optional. Multiple specialized Headless AI Agents divide responsibilities such as implementation, testing, review, or other roles.

A Crew does not require direct agent-to-agent communication. Coordination may happen through Ralph, direct interaction, or shared external state such as Git commits, worktrees, issues, files, and test results.

## Rules

- MUST use one general-purpose Headless AI Agent when no Crew is configured.
- MUST treat Crew as an explicit Loop configuration, not as the default execution mode.
- MUST give each Crew agent a distinct responsibility or role.
- MUST coordinate all Crew agents toward one shared task outcome.
- MAY coordinate agents through direct interaction, Ralph orchestration, shared external state, or a combination of these.
- MUST NOT require direct cross-agent communication for a configuration to qualify as a Crew.
- SHOULD keep durable handoff state outside conversational memory when agents execute in separate sessions.
- SHOULD use the smallest Crew needed for the task; a role without distinct responsibility SHOULD remain with the general-purpose agent.

## Example

Default execution:

```text
Ralph
  └─ General-purpose Headless AI Agent
       └─ implement + test + review task
```

Crew execution:

```text
Ralph
  ├─ Codey  → implementation → Git commit
  ├─ Testy  → reads commit → test results
  └─ Chorey → reads commit/results → review
```

The agents may never communicate directly; Git and test results can provide the handoff between roles.

## Benefits and Trade-offs

**Benefits**

- General-purpose mode keeps simple tasks cheap and easy to orchestrate.
- Crew mode isolates responsibilities and allows specialized prompts, tools, and verification.
- Shared-state coordination keeps agents independently executable.

**Trade-offs**

- Crew mode adds orchestration and handoff overhead.
- Poor role boundaries can duplicate work or produce conflicting changes.
- More agents do not inherently improve task quality.

## Validation

A conforming Loop configuration runs one general-purpose agent when no Crew is configured. When a Crew is configured, it assigns distinct roles toward one shared outcome and can coordinate those roles without requiring direct cross-agent communication.

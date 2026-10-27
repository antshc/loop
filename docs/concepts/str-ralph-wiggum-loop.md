# Ralph Wiggum Loop
**Type:** Architecture Pattern

## Purpose

Provide a reusable autonomous development-loop pattern that can continue across bounded AI coding-agent sessions without accumulating degraded conversational context. The pattern defines the invariants; concrete orchestration strategies are selectable options.

## Concept

A Ralph Wiggum loop repeatedly starts fresh AI coding-agent sessions against durable external state. Each iteration reconstructs the current task state from artifacts such as issues, a task list, PRD, source tree, Git history, or test results, performs bounded work, then persists progress for the next iteration.

The orchestration strategy is replaceable. Every option MUST preserve fresh sessions, external state, bounded progress, durable handoff, and verifiable completion.

## Options

### Simple Loop

Repeatedly invoke one coding agent with a stable prompt. Each iteration is fresh, rebuilds current external state, completes one issue, verifies it, commits the result, and closes the issue. No planner/reviewer orchestration.

#### Rules

- MUST use a simple bounded loop, such as `max_iterations = 3`, to repeatedly invoke the coding agent.
- MUST start a new agent invocation for every iteration; previous conversational context MUST NOT be resumed.
- MUST load the same stable prompt file for every iteration.
- MUST rebuild dynamic prompt state every iteration:
  - current actionable issues from the task source;
  - recent `RALPH:` commits from Git history.
- MUST instruct the agent to work on exactly one issue per iteration.
- MUST keep cross-iteration state in GitHub issues, Git commits, source files, and tests rather than previous chat context.
- MUST run the configured verification commands before committing or closing the issue.
- MUST commit completed work with the `RALPH:` prefix and close the completed issue so the next fresh iteration sees the updated task list and history.
- MUST stop early when the agent emits `<promise>COMPLETE</promise>`, which means no actionable issues remain.
- MUST stop when the iteration limit is reached even if completion was not signaled.

#### TypeScript example

```ts
import { readFileSync } from 'node:fs';

const MAX_ITERATIONS = 3;
const PROMPT_FILE = '.loop/prompt.md';
const COMPLETION_SIGNAL = '<promise>COMPLETE</promise>';

async function sh(...args: string[]): Promise<string> {
  const child = Bun.spawn(args, { stdout: 'pipe', stderr: 'inherit' });
  const [stdout, exitCode] = await Promise.all([child.stdout.text(), child.exited]);
  if (exitCode !== 0) {
    throw new Error(`${args.join(' ')} failed with exit status ${exitCode}`);
  }
  return stdout.trim();
}

async function buildPrompt(): Promise<string> {
  const template = readFileSync(PROMPT_FILE, 'utf8');

  // Re-evaluated every iteration.
  const openIssues = await sh('bun', 'scripts/list-tasks.ts');
  const recentRalphCommits = await sh('git', 'log', '--oneline', '--grep=RALPH', '-10');

  return template
    .replace('{{LIST_TASKS_COMMAND}}', openIssues)
    .replace('{{GIT_HISTORY}}', recentRalphCommits);
}

async function runAgent(prompt: string): Promise<string> {
  // New process => fresh agent session.
  const child = Bun.spawn(['copilot', '-p', prompt], { stdout: 'pipe', stderr: 'inherit' });
  const [stdout] = await Promise.all([child.stdout.text(), child.exited]);
  return stdout;
}

for (let iteration = 0; iteration < MAX_ITERATIONS; iteration++) {
  const output = await runAgent(await buildPrompt());

  if (output.includes(COMPLETION_SIGNAL)) {
    break;
  }
}
```

The stable prompt carries the per-iteration workflow:

```text
# Context

## Open issues
{{LIST_TASKS_COMMAND}}

## Recent RALPH commits
{{GIT_HISTORY}}

# Task

Work on one issue only.

Before commit:
1. npm run typecheck
2. npm run test

When complete:
1. git commit with a message starting with "RALPH:"
2. close the issue

If no actionable issues remain, output:
<promise>COMPLETE</promise>
```

Additional orchestration options may be added as sibling sections under **Options**.

## Benefits and Trade-offs

**Benefits**

- Fresh context reduces context rot, drift, and accumulation of incorrect assumptions.
- GitHub issues and Git commits make progress resumable across independent runs.
- Verification gates keep issue completion externally checkable.
- The loop stays small and understandable.

**Trade-offs**

- Fresh sessions may spend tokens rediscovering repository and task context.
- Simple Loop serializes work.
- Correctness depends on the prompt and task-list command exposing enough durable state.

## Validation

A conforming Simple Loop demonstrates that each iteration launches a new agent process, rebuilds current task and Git state, handles one issue, verifies before commit/close, persists progress through Git and the issue tracker, and terminates on `<promise>COMPLETE</promise>` or the iteration limit.

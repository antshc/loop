# Lifecycle Hooks
**Type:** Architecture Pattern

## Purpose

Let a repository prepare a fresh worktree (copy `.env`, install dependencies) and react to a run's end without baking repository-specific setup into Loop workflows.

## Concept

A Loop hook is a user-declared shell command that Loop runs at a named Loop hook point in a run's lifecycle. Loop defines three points, each a `LoopHook` subclass that fixes its point: `worktree-ready` (in the fresh worktree, before the agent starts), `worktree-removing` (in the worktree after the safety-net commit, before removal, on success and on failure), and `run-finished` (in the repository after the worktree is merged and removed, only when the run succeeded). Loop hooks run on the host, so a hook that needs a different environment carries that in its own command.

The workflow declares its hooks as code in `GitOptions(loop_hooks=...)`; the worktree strategy runs them at each point in declared order. A failing or timed-out hook raises `LoopHookError` carrying the point, command, and output; a failing `worktree-ready` hook force-removes the worktree, so the caller gets a ready worktree or none. Loop hooks need a worktree strategy, not `HeadStrategy`.

Modelled on Sandcastle's host-side `onWorktreeReady` hook ([research](../research/sandcastle-agent-invocation-and-extension-points.md)). Hooks that an agent CLI fires natively are Agent CLI hooks (`with_agent_cli_hooks`), a separate term: they only observe and never fail or steer a run.

## Rules

- MUST declare a hook as a shell command with an optional per-hook timeout, using the `LoopHook` subclass of its point.
- MUST run hooks on the host, with the worktree (`worktree-ready`, `worktree-removing`) or the repository (`run-finished`) as working directory.
- MUST run the hooks of one Loop hook point sequentially in declared order.
- MUST fail the run with `LoopHookError` when a hook exits non-zero or times out; a `worktree-ready` failure MUST remove the worktree before the agent starts.
- MUST bound every hook with a default timeout that a hook can override.
- MUST limit `worktree-ready` hooks to ignored paths, so the worktree stays clean for the agent.
- MUST NOT let a hook change which agent, prompt, or Ticket the run uses.

## Benefits and Trade-offs

**Benefits**

- Repository-specific setup stays in the repository, language-neutral.
- A failed setup is reported before any agent time is spent.

**Trade-offs**

- Commands run on the host with the user's privileges.
- No cancellation signal: a hook is bounded only by its timeout.

## Validation

A run whose `worktree-ready` hook exits non-zero or exceeds its timeout never starts the agent and raises `LoopHookError` for the failing command; a successful hook's effects are visible in the worktree the agent receives.

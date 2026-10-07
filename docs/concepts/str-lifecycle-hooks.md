# Lifecycle Hooks
**Type:** Architecture Pattern

## Purpose

Let a repository prepare a fresh worktree (copy `.env`, install dependencies) before a Headless AI Agent starts, without baking repository-specific setup into Loop workflows.

## Concept

A Hook is a user-declared shell command that Loop runs at a named Hook point in a run's lifecycle. Loop defines two Hook points, both before the agent starts: `worktree-ready`, after the worktree is created, and `sandbox-ready`, after the Sandbox is up. Hooks run on the host, so a hook that needs a different environment carries that in its own command.

The workflow declares its hooks as code in `SandboxHooks` and passes them to `create_sandbox(..., hooks=...)`, which runs the `worktree-ready` and `sandbox-ready` hooks through `run_host_hooks`, the first right after `WorktreeService.create` and the second once the Sandbox starts. `GitClient.run_hook` owns execution, timeout, and cancellation; a failing hook closes the Sandbox, removes the worktree, and raises `HookError`, so the caller gets a ready Sandbox or none. Hooks exist only before the agent: post-iteration work is an explicit step of the workflow's use case, not a hook. `with_sandbox_lifecycle` accepts its own `on_sandbox_ready` hooks for standalone use; `Sandbox.run` passes none because setup already ran them.

Modelled on Sandcastle's host-side `onWorktreeReady` and `onSandboxReady` hooks ([research](../research/sandcastle-agent-invocation-and-extension-points.md)); its sandbox-side hooks have no Loop counterpart (Loop has no Sandbox-side hooks).

## Rules

- MUST declare a hook as a shell command with an optional per-hook timeout, and nothing else.
- MUST run hooks only at a named Hook point before the agent starts; MUST NOT run hooks after the agent.
- MUST run hooks on the host with the worktree as working directory.
- MUST run the hooks of one Hook point sequentially in declared order.
- MUST fail the run before the agent starts when a hook exits non-zero or times out, removing the worktree before raising.
- MUST bound every hook with a default timeout that a hook can override.
- MUST cancel in-flight hooks when the workflow-supplied `threading.Event` is set; the run then reports cancelled, not failed.
- MUST NOT let a hook change which agent, prompt, or Ticket the run uses.

## Benefits and Trade-offs

**Benefits**

- Repository-specific setup stays in the repository, language-neutral.
- A failed setup is reported before any agent time is spent.

**Trade-offs**

- No post-agent hook: verification, cleanup, or notification must be a workflow step.
- Commands run unsandboxed on the host with the user's privileges.

## Validation

A run whose `worktree-ready` or `sandbox-ready` hook exits non-zero or exceeds its timeout never starts the agent and reports the failing command; a successful hook's effects are visible in the worktree the agent receives.

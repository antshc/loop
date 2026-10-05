# Host vs sandbox actions in `createSandbox()` and `sandbox.run()`

"Sandbox" means the container (Docker bind-mount provider). With `noSandbox()` everything runs on the host. Isolated providers sync commits back through `applyToHost` (`syncOut`) instead of sharing a mount; that path is not covered here.

## `createSandbox()` (setup, `src/createSandbox.ts`)

### Host

1. Resolve the repo dir (`cwd` or `process.cwd()`).
2. Prune stale worktrees, then `git worktree add` at `.sandcastle/worktrees/<branch>/`. The branch is created from `baseBranch` (default `HEAD`) if it does not exist.
3. Copy `copyToWorktree` paths from the repo into the worktree (bind-mount and no-sandbox only).
4. Run `hooks.host.onWorktreeReady` with `cwd` set to the worktree.
5. Read `.sandcastle/.env` and merge env vars.
6. Read the repo's `.git` and resolve the extra git mounts.
7. Start the container, with the worktree mounted at `/home/agent/workspace` and `.git` mounted too.
8. Run `hooks.host.onSandboxReady` with `cwd` set to the worktree.
9. Register a SIGINT/SIGTERM handler that prints "Worktree preserved at ...".

### Sandbox

- If any `onSandboxReady` hook exists, run `git config --global --add safe.directory <repo>` so git trusts the mounted worktree.
- Run `hooks.sandbox.onSandboxReady` with `cwd` set to `/home/agent/workspace`.

If setup fails after the worktree is created, the container is stopped and the worktree is removed.

## `sandbox.run()` (each call, `src/createSandbox.ts`, `src/SandboxLifecycle.ts`)

### Host

- Check that `resumeSession` exists on the host, if set.
- Resolve the prompt file, read the current host branch, and substitute `{{ARGS}}`. `SOURCE_BRANCH` is the worktree branch and `TARGET_BRANCH` is the host's current branch.
- Set up logging, either to `.sandcastle/logs/` or stdout.
- Record `baseHead` with `git rev-parse HEAD` in the worktree.
- Count new commits (`git rev-list baseHead..HEAD`) and return them in the result.
- Copy the agent's session file back to the host after each iteration (`captureToHost`).
- `merge-to-head` only: `git merge <temp branch>` in the host repo, then delete the temp branch. This does not happen for an explicit `branch` strategy.

### Sandbox

- Expand shell expressions in the prompt (`preprocessPrompt` runs them in the sandbox).
- Copy the session file into the container when resuming (`resumeIntoSandbox`).
- Run the agent process itself: file edits, tests, and `git commit`. Commits are written through the mount into the host worktree and branch.
- `merge-to-head` only: `git checkout --detach` before the temp branch is deleted.

## Shared state and cleanup

- The worktree is bind-mounted, so the host sees the agent's edits and commits immediately. There is no copy step.
- `sandbox.close()` stops the container. It removes the worktree unless the worktree has uncommitted changes, in which case it returns `preservedWorktreePath`. The branch itself is kept.
- `sandbox.exec()` runs commands inside the sandbox, with `cwd` defaulting to the repo path.

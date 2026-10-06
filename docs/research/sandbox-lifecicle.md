/home/pet/_projects/sandcastle/src/SandboxLifecycle.ts

withSandboxLifecycle in SandboxLifecycle.ts wraps one unit of agent work (a run() or similar) with setup and teardown around an already-created sandbox. It is an Effect function, withSandboxLifecycle(options, sandbox, work). It returns { result, branch, commits }, where result is whatever work returned.

Steps, in order

Resolve timeouts and signal: timeouts come from options.timeouts and fall back to defaults. The abort signal falls back to a never-aborted one.
Record host state:
It records the host's current branch, but only when no explicit branch was given.
It reads the host git config name and email.
Set up the sandbox (inside a "Setting up sandbox" task log):
It marks the repo as a git safe.directory.
It copies the host git identity into the sandbox, so commits are attributed to the developer.
It discovers the branch the worktree is on.
It runs onSandboxReady hooks. Sandbox hooks and host hooks run in parallel, and each has a timeout (default 60s). They can be aborted through the signal.
Git setup commands are retried on transient exec exit codes (126 and 137).
Record baseHead: git rev-parse HEAD on the host-side worktree.
Run work({ sandbox, sandboxRepoDir, baseHead }): this is the agent run.
Sync out: for isolated providers, applyToHost() copies the sandbox's commits back to the host. For bind-mount providers it is a no-op.
Collect commits:
Temp-branch mode (no branch): it merges the temp branch into the host's current branch, then deletes the temp branch. keepSourceBranch skips the detach and delete.
Explicit branch: the commits stay on that branch.
Both modes: it runs git rev-list baseHead..tip to build commits.
Related exports

runHostHooks: runs host-side hook commands one after another, failing fast on a non-zero exit.
SandboxHooks: the hook configuration type.
SandboxLifecycleOptions: the options type, including branch, applyToHost, signal, timeouts and keepSourceBranch.
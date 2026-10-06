/home/pet/_projects/sandcastle/src/syncOut.ts

result.commits is the list of commits ({ sha: string }[]) the agent added during that sandbox.run(). Oldest is first. It's built in SandboxLifecycle.ts:365-543.

How it's counted:

Baseline: before the agent runs, the lifecycle records baseHead with git rev-parse HEAD in the host-side worktree.
Sync: after the agent finishes, an isolated sandbox syncs its commits back to the host first. This creates new SHAs via format-patch/am.
Collection: it then runs git rev-list <baseHead>..<tip> --reverse on the host. This returns every commit reachable from the tip but not from baseHead, oldest first.
Explicit branch: the tip is refs/heads/<branch>. If that branch doesn't exist, the list is empty.
Temp-branch mode (no branch): the temp branch is first merged into the host's current branch, then deleted. The tip is HEAD.
Failure and timeout: any git error gives []. A timeout raises CommitCollectionTimeoutError. The limit is commitCollectionTimeoutMs, default 30s.
In your run.ts, result.commits.length > 0 means the Implementer produced at least one new commit on issue.branch. That is the gate for running the Reviewer and for including the branch in the merge.

Merge commits count too, because rev-list includes them. A run with no new commits returns [].
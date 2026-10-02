---
name: create-worktree
description: Create or reuse an isolated git worktree for a feature branch in the given codebase repo path, based off the existing origin/<feature-branch> when one exists, otherwise off origin/<target-branch>. Branch detection is the caller's responsibility.
argument-hint: '<codebase-repo-path> <target-branch> <feature-branch>'
---

# Create Worktree

Create or reuse an isolated git worktree for a feature branch based off a target branch.

**Arguments:** `<codebase-repo-path> <target-branch> <feature-branch>`

The path is supplied by the caller — this skill performs no resolution of its own.

```bash
CODEBASE_REPO_PATH=<codebase-repo-path>
git -C "$CODEBASE_REPO_PATH" rev-parse --is-inside-work-tree
```

If this fails (path missing or not a git repo), **exit** and report `Not a git repository: $CODEBASE_REPO_PATH` before running any other command.

```bash
cd "$CODEBASE_REPO_PATH"
```

All subsequent commands run inside `CODEBASE_REPO_PATH`. Worktrees are created as `<CODEBASE_REPO_PATH>.worktrees/<feature-branch>`.

## Actualize Branch

Bring the current branch up to date with the latest remote changes and the target branch. Run whenever entering an existing branch.

```bash
git fetch --all --prune
git pull
git merge origin/<target-branch>
```

If the merge exits non-zero (conflicts detected):

1. Collect the list of conflicting files:
   ```bash
   git diff --name-only --diff-filter=U
   ```
2. Resolve each conflict directly in the current context, preserving the intent of both sides.
3. After resolving the conflicts:
   ```bash
   git add .
   git merge --continue --no-edit
   ```
4. If the merge still fails, **exit** and report the unresolved files as blockers.

## 1. Check current branch

```bash
current_branch=$(git branch --show-current)
```

If `current_branch` equals `<feature-branch>`:

```bash
WORKTREE_PATH=$(pwd)
BRANCH=$current_branch
```

Run **Actualize Branch**, then jump to **Output**.

## 2. Create worktree

```bash
repo_root=$CODEBASE_REPO_PATH
mkdir -p "$repo_root.worktrees"
git fetch --all --prune
if git rev-parse --verify --quiet "origin/<feature-branch>" > /dev/null; then
  base_ref="origin/<feature-branch>"
else
  base_ref="origin/<target-branch>"
fi
git worktree add -b <feature-branch> "$repo_root.worktrees/<feature-branch>" "$base_ref"
```

Preferring an existing `origin/<feature-branch>` preserves commits from a prior run on the same branch; falling back to `origin/<target-branch>` is the fresh-branch case.

- If the worktree already exists (exit code 128), `cd` into it and run **Actualize Branch**:
  ```bash
  cd "$repo_root.worktrees/<feature-branch>"
  ```
- If creation fails for any other reason, **exit** and report the error.

## 3. Switch into worktree

```bash
cd "$repo_root.worktrees/<feature-branch>"
```

## Output

Report the result:

```
CODEBASE_REPO_PATH: $CODEBASE_REPO_PATH
WORKTREE_PATH: $repo_root.worktrees/<feature-branch>
BRANCH: <feature-branch>
TARGET_BRANCH: <target-branch>
```

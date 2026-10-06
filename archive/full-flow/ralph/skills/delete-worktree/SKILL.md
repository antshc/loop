---
name: delete-worktree
description: Remove a git worktree and delete its local feature branch once development on it is finished. Never touches the remote branch or an open PR.
argument-hint: '<codebase-repo-path> <worktree-path> <branch>'
---

# Delete Worktree

Remove the local worktree and delete the local feature branch. Run this once development on the branch is finished for the invocation (e.g. after a PR is created/pushed) — never mid-loop while more commits on the branch are still expected.

**Arguments:** `<codebase-repo-path> <worktree-path> <branch>`

## 1. Remove worktree

Worktree removal must run from the main working tree, not from inside the worktree itself:

```bash
cd "<codebase-repo-path>"
git worktree remove "<worktree-path>" --force
```

## 2. Delete local branch

```bash
git branch -D "<branch>"
```

## Rules

- Only removes the local worktree and local branch. Never deletes `origin/<branch>` — an open PR or a later `/create-worktree` run may still depend on it.
- If either command fails, report the error but treat it as non-fatal to the calling skill's overall result.

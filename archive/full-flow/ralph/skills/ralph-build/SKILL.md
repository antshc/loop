---
name: ralph-build
description: Build the project in the given workspace. Run after worktree setup, before implementation.
argument-hint: '<harness-repo-path> <workspace-path>'
---

# Build

**Arguments:** `<harness-repo-path> <workspace-path>`

Both paths are supplied by the caller — this skill performs no resolution of its own.

**Build** the project in `<workspace-path>` using the "Build the solution" instructions in `<harness-repo-path>/README.md`. On failure, report and stop — never explore a broken build.

**Emit**: "Build: pass | fail."

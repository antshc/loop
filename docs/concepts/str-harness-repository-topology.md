# Harness Repository Topology
**Type:** Architecture Pattern

## Purpose

Ralph skills run against a **harness** (where docs, specs, instructions, and skills live and where the agent is launched) while changing code in a **codebase** (the repository the work targets). Teams lay these out differently — one repository holding both, or one harness wrapping several codebases — and every skill must resolve the same paths in both layouts without per-skill special cases.

## Concept

The **harness repo** is the root from which the Workflow is run; it supplies the agent's instructions and skills. The **codebase checkout** is the repository whose code is changed. Both are the same repository in the single-repo layout and different repositories in the multi-repo layout. Code is never changed in the checkout itself: every change happens in a **worktree** placed in the harness's `workspace` folder, in a folder named after the checkout with a `.worktrees` suffix, one subfolder per feature branch.

Workflows resolve the harness path first, then resolve the codebase checkout through a `RepositoryPool`: a per-developer list of `Repository` entries (checkout path, `owner/name`, and an `is_harness` flag marking exactly one entry as the harness) that the user declares once, after manually checking out and placing each repository themselves. The pool looks a Spec's target up by `owner/name`, case-insensitively, and hands back its configured path and `owner/name`-bound pull request client; there is no git check of the checkout's existence or its `origin` remote.

Work is split by repository: spec, issue, and documentation operations (and the final harness commit and push) target the harness; code, build, and pull request operations target the worktree.

## Rules

- MUST default the harness path to the folder the Workflow script is run from (the user runs it from the harness repo, not from a git top-level lookup); a workflow sets it as a coded setting and may expose its own argument to override it; fail the whole run before touching any spec when the `RepositoryPool` has zero or more than one `is_harness` entry.
- MUST resolve the codebase checkout by looking the target repository up in the `RepositoryPool`, keyed by `owner/name`; the pool's `is_harness`-flagged entry is the harness, every other entry a workspace checkout — both are declared by the user, not derived from git.
- MUST NOT check the checkout's existence or its `origin` remote against the target repository; the user is responsible for checking out and keeping each repository's clone at its configured path. On a target with no entry in the `RepositoryPool`, create no worktree and touch no review thread, label the spec `hitl` with a comment naming the unconfigured target, and continue with the other specs.
- MUST NOT clone a missing checkout during a run.
- MUST take the target repository from the spec's `repo:target:<owner/name>` label; a missing, malformed, or duplicated label is handled like an unconfigured target, with no default to the harness.
- MUST send spec and ticket operations through a platform instance bound to the harness repository and pull request operations through a separate instance bound to the resolved target repository; the `RepositoryPool` builds each entry's pull request client once, at startup.
- MUST use the `RepositoryPool`'s configured list as the only source of a checkout's path; no git identity check, no fallback derivation.
- MUST create each worktree as `<harness>/workspace/<checkout folder name>.worktrees/<feature-branch>` in both layouts, never in the checkout's tracked tree, and add `workspace/` to the checkout's `.git/info/exclude` when creating one.
- MUST run code, build, and pull request commands inside the worktree and spec, issue, and documentation commands against the harness repo.
- MUST keep the harness's docs, `.github` instructions, and skills in the harness repo, whichever layout is in use.
- MUST keep the list of workspace repositories in the per-developer, gitignored harness settings, not in version control.

### Variant: Single repo
**Selected when:** the harness and the only target repository are the same repository.

- MUST treat the harness root as the codebase checkout; its `workspace` folder holds only worktrees.
- MUST place worktrees in `<harness>/workspace/<harness folder name>.worktrees/`, excluded from version control through `.git/info/exclude`.

### Variant: Multi repo
**Selected when:** the harness wraps one or more target repositories that are not the harness itself.

- MUST keep each target repository as its own clone inside the harness's workspace folder, named after the repository, and its worktrees beside that clone inside the workspace folder.
- MUST run the Workflow from the harness root so the worktree root sits inside the process working directory; the agent's own working directory is the worktree, so harness `.github` customizations reach it only when the Workflow passes the harness as an additional directory.
- MUST keep a VS Code multi-root workspace file in the harness listing the harness root and each workspace repository as folders.
- MUST enable in that file the settings that load instruction files, load customizations from parent repositories, and allow multi-root agent sessions, so harness instructions apply to the workspace repositories.
- MUST keep the workspace repositories out of the harness's version control.

## Example

Single repo — harness and codebase are one repository:

```text
loop/                         # harness root = codebase checkout
├── .github/                      # instructions, skills
├── CONTEXT.md
├── ARCHITECTURE.md
├── docs/
├── src/loop/
├── workflows/
├── tests/
└── workspace/                    # excluded from version control
    └── loop.worktrees/
        └── <feature-branch>/     # worktree; all code changes happen here
```

Multi repo — harness wraps the codebase repositories:

```text
acme-board/                       # harness root; agent starts here
├── .github/                      # harness instructions, skills
│   └── skills/harness/           # harness skill + per-developer settings
├── docs/
├── acme-board.code-workspace     # multi-root workspace file
└── workspace/
    ├── acme-api/                 # codebase checkout
    └── acme-api.worktrees/       # worktrees of that checkout
        └── <feature-branch>/
```

Workspace file of the multi repo harness:

```jsonc
{
  "folders": [
    { "name": "acme-board", "path": "." },
    { "name": "acme-api", "path": "workspace/acme-api" }
  ],
  "settings": {
    "github.copilot.chat.codeGeneration.useInstructionFiles": true,
    "chat.agentHost.copilotAgent.multiRootEnabled": true,
    "chat.useCustomizationsInParentRepositories": true,
    "github.copilot.chat.agentDebugLog.fileLogging.enabled": false,
    "chat.agentHost.agentDebugLog.enabled": false,
    "dotnet.defaultSolution": "workspace/acme-api/all.sln",
    "files.autoSave": "onFocusChange",
    "search.useIgnoreFiles": false,
    "search.exclude": {
      "**/bin": true,
      "**/obj": true,
      "**/TestResults": true,
      "graphify-out": true,
      "workspace/acme-api/bin/review_diff": true
    },
    "csdevkit.dotnetSkillsEnabled": false
  }
}
```
# Harness Repository Topology
**Type:** Architecture Pattern

## Purpose

Ralph skills run against a **harness** (where docs, specs, instructions, and skills live and where the agent is launched) while changing code in a **codebase** (the repository the work targets). Teams lay these out differently — one repository holding both, or one harness wrapping several codebases — and every skill must resolve the same paths in both layouts without per-skill special cases.

## Concept

The **harness repo** is the root from which the agent is executed; it supplies the agent's instructions and skills. The **codebase checkout** is the repository whose code is changed. Both are the same repository in the single-repo layout and different repositories in the multi-repo layout. Code is never changed in the checkout itself: every change happens in a **worktree** placed beside the checkout in a sibling folder named after it with a `.worktrees` suffix, one subfolder per feature branch.

Workflows resolve the harness path first, then derive the codebase checkout by comparing the target repository's identity (`owner/name`) with the harness's own `origin` remote: equal → the harness is the checkout; different → the checkout is the repository folder of that name inside the harness's `workspace` folder, whose own `origin` must equal the target. The configured repository list never takes part in this decision. Identities are compared case-insensitively after normalizing ssh, https, and `.git` forms; only `github.com` remotes resolve.

Work is split by repository: spec, issue, and documentation operations (and the final harness commit and push) target the harness; code, build, and pull request operations target the worktree.

## Rules

- MUST default the harness path to the git top-level of cwd (the user runs Orb from the harness repo); a workflow sets it as a coded setting and may expose its own argument to override it; when the resolved path is not in a git repository or the harness `origin` is absent or not `github.com`, fail the whole run before touching any spec.
- MUST derive the codebase checkout by comparing the target repository with the harness `origin`: equal → checkout is the harness; otherwise → checkout is `<harness>/workspace/<repository name>`.
- MUST confirm the checkout exists and its own `origin` matches the target repository; on a missing or mismatched checkout, create no worktree and touch no review thread, label the spec `hitl` with a comment naming the expected path and actual `origin`, and continue with the other specs.
- MUST NOT clone a missing checkout during a run.
- MUST take the target repository from the spec's `repo:target:<owner/name>` label; a missing, malformed, or duplicated label is handled like a missing checkout, with no default to the harness.
- MUST send spec and ticket operations through a platform instance bound to the harness repository and pull request operations through a separate instance bound to the resolved target repository.
- MUST NOT use the configured repository list to choose a checkout; it is only the input of the pull command.
- MUST create each worktree beside its checkout as `<checkout>.worktrees/<feature-branch>`, never inside the checkout.
- MUST run code, build, and pull request commands inside the worktree and spec, issue, and documentation commands against the harness repo.
- MUST keep the harness's docs, `.github` instructions, and skills in the harness repo, whichever layout is in use.
- MUST keep the list of workspace repositories in the per-developer, gitignored harness settings, not in version control.

### Variant: Single repo
**Selected when:** the harness and the only target repository are the same repository.

- MUST treat the harness root as the codebase checkout; the workspace folder does not exist.
- MUST place worktrees beside the harness root, outside the repository tree.

### Variant: Multi repo
**Selected when:** the harness wraps one or more target repositories that are not the harness itself.

- MUST keep each target repository as its own clone inside the harness's workspace folder, named after the repository, and its worktrees beside that clone inside the workspace folder.
- MUST run the agent from the harness root so the harness `.github` customizations apply, while code changes land in the workspace repositories.
- MUST keep a VS Code multi-root workspace file in the harness listing the harness root and each workspace repository as folders.
- MUST enable in that file the settings that load instruction files, load customizations from parent repositories, and allow multi-root agent sessions, so harness instructions apply to the workspace repositories.
- MUST keep the workspace repositories out of the harness's version control.

## Example

Single repo — harness and codebase are one repository:

```text
orb/                         # harness root = codebase checkout
├── .github/                      # instructions, skills
├── CONTEXT.md
├── ARCHITECTURE.md
├── docs/
├── ralph/
└── tools/
orb.worktrees/               # sibling of the checkout
└── <feature-branch>/             # worktree; all code changes happen here
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
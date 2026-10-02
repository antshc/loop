# ralph plugin

## Agents

Both agents are from the `crew` plugin and are invoked by `/dev` via `runSubagent`; see [dev/SKILL.md](dev/SKILL.md) steps 3 and 6 for their prompts and gating.

| Agent | Role | Defined in |
|-------|------|-----------|
| `codey-py`, `codey-ai`, `codey-dotnet` | Stack-specific implementers selected per task; unmatched work uses `general-purpose` | [crew agents](../../crew/agents) |
| `chorey` | Maintainability-review agent — reviews Codey's staged changes in step 6, gated on `STATUS: complete`; its own `STATUS` never overrides Codey's recorded outcome | [`plugins/crew/agents/chorey.agent.md`](../../crew/agents/chorey.agent.md) |

**Via `/dev` skill** (fully automated — fetches the spec's sub-tickets, picks tasks, loops):

```
/dev <spec-issue-number-or-url>
```

## Skills

| Skill | Description |
|-------|-------------|
| `/dev` | AFK loop — picks next issue, invokes `codey` then (gated) `chorey`, pushes |
| `/fix` | Apply PR review comments |
| `/address` | Address a PR's review discussion — group into issues, investigate, fix, reply; rerunnable |
| `/init-harness` | One-time setup for a multi-repo/wrapping harness — generates the repository-level `harness` skill and its pull command |
| `/ralph-build` | Build the project in a caller-supplied workspace, using the harness repo's README build instructions |
| `/create-worktree` | Create/reuse an isolated git worktree in the caller-supplied codebase repo path |
| `/delete-worktree` | Remove a worktree and delete its local feature branch once development is finished (remote branch/PR untouched) |

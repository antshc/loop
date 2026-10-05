# Keep commit, push, pull request, and ticket-state changes in Python

`orb dev` runs the agent against a worktree and must then publish its work. Orb's Python workflow performs the commit, push, draft pull request, and ticket close or label itself; the agent is told not to commit or push and returns a machine-readable report that Python acts on.

## Considered Options

- **Agent commits and closes tickets through the prompt (Sandcastle shape)** — rejected: state changes depend on model judgement, so they are not deterministic and cannot be tested against fakes.
- **Skill-driven orchestration inside the agent session (the current `/ralph:dev` skill)** — rejected: Python only starts the session, so commit, push, PR, and ticket outcomes are invisible to it and untestable.

## Consequences

- The agent's report is a contract: Python can only close, label, or commit what the report states.
- Failures of `git`, `gh`, and the report parse are Python errors, handled by workflow code rather than by prompt instructions.
- The agent prompt must forbid commit and push, and agent permissions can deny them.

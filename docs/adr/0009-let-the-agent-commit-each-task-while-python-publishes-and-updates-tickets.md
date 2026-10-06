# Let the agent commit each task while Python publishes and updates Tickets

The `dev` Workflow's agent works through the Spec's actionable Tickets in one session, orchestrating its own loop: select the next task, implement it, commit it. The agent makes each commit in the worktree with a `ccode:` subject prefix, and Python reads the recent `ccode:` commits into the next run's prompt as state. Python never commits; once the run ends it pushes the commits the agent made, ensures the draft pull request, and closes, comments on, or labels Tickets from the agent's report. This reverses only the commit part of [ADR 0006](0006-keep-commit-push-pull-request-and-ticket-state-changes-in-python.md).

## Considered Options

- **Python commits once after the run from the report (ADR 0006)** — rejected: one commit mixes every Ticket the agent worked on, so a later run cannot see which Ticket a commit delivered and the agent cannot checkpoint its own progress.
- **The agent also pushes and closes Tickets (full `/ralph:dev` shape)** — rejected: push, pull request, and Ticket state stay deterministic and testable against fakes only when Python owns them.
- **Python loops and starts one fresh agent run per task** — rejected for now: it needs a second source of per-iteration state; the in-session loop reuses the recent commits as its only handoff.

## Consequences

- Commit subjects starting with `ccode:` are a contract: they are how a rerun tells which work is already delivered.
- Work the agent leaves uncommitted is not saved; it is discarded with the worktree.
- The agent prompt must still forbid push and issue changes, and agent permissions can deny them.
